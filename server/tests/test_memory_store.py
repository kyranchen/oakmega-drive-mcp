"""Token store semantics, exercised against the in-memory backend.

These are the rules the Firestore backend must also honour: single use, expiry
on read, and no raw token on disk. The memory backend gets read-and-delete
atomicity from the GIL; Firestore has to reproduce it with a transaction.
"""

import datetime
import time

import pytest
from mcp.server.auth.provider import AccessToken, AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull

from app.storage.base import GoogleCredentials, PendingAuthorization
from app.storage.memory_store import MemoryTokenStore


def _code(name: str = "code-1", expires_in: float = 600) -> AuthorizationCode:
    return AuthorizationCode(
        code=name,
        scopes=["drive.readonly"],
        expires_at=time.time() + expires_in,
        client_id="client-1",
        code_challenge="challenge",
        redirect_uri="http://localhost:1/cb",
        redirect_uri_provided_explicitly=True,
        subject="google-sub-1",
    )


def _pending(age_minutes: float = 0) -> PendingAuthorization:
    return PendingAuthorization(
        client_id="client-1",
        redirect_uri="http://localhost:1/cb",
        redirect_uri_provided_explicitly=True,
        client_state="client-state",
        code_challenge="challenge",
        scopes=["drive.readonly"],
        resource=None,
        created_at=datetime.datetime.now(datetime.UTC)
        - datetime.timedelta(minutes=age_minutes),
    )


@pytest.fixture
def store() -> MemoryTokenStore:
    return MemoryTokenStore()


class TestAuthorizationCodes:
    async def test_a_code_can_only_be_redeemed_once(self, store):
        """A second presentation is a replay, not a retry."""
        await store.put_code(_code())

        assert await store.pop_code("code-1") is not None
        assert await store.pop_code("code-1") is None

    async def test_expired_codes_are_refused(self, store):
        await store.put_code(_code("stale", expires_in=-1))
        assert await store.pop_code("stale") is None

    async def test_expired_codes_are_also_removed(self, store):
        await store.put_code(_code("stale", expires_in=-1))
        await store.pop_code("stale")
        assert await store.get_code("stale") is None

    async def test_get_code_does_not_consume(self, store):
        """The SDK verifies PKCE against the record before calling exchange, so
        the read has to be non-destructive and the delete belongs in pop."""
        await store.put_code(_code())

        assert await store.get_code("code-1") is not None
        assert await store.get_code("code-1") is not None
        assert await store.pop_code("code-1") is not None

    async def test_subject_survives_the_round_trip(self, store):
        """Identity travels on this field; losing it makes tool calls anonymous."""
        await store.put_code(_code())
        assert (await store.pop_code("code-1")).subject == "google-sub-1"


class TestPendingAuthorizations:
    async def test_a_parked_request_can_only_be_resumed_once(self, store):
        await store.put_pending("state-1", _pending())

        assert await store.pop_pending("state-1") is not None
        assert await store.pop_pending("state-1") is None

    async def test_stale_requests_are_refused(self, store):
        await store.put_pending("old", _pending(age_minutes=30))
        assert await store.pop_pending("old") is None

    async def test_unknown_state_returns_none(self, store):
        """An unrecognised state means expired or forged. Failing closed here is
        the CSRF protection, not merely defensive coding."""
        assert await store.pop_pending("never-issued") is None


class TestCredentialsAndTokens:
    async def test_credentials_round_trip(self, store):
        creds = GoogleCredentials(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.datetime.now(datetime.UTC),
            scopes=["drive.readonly"],
        )
        await store.put_credentials("sub-1", creds)

        assert (await store.get_credentials("sub-1")).refresh_token == "refresh"

    async def test_unknown_subject_returns_none_rather_than_raising(self, store):
        """The provider turns None into a well-formed OAuth error; an exception
        would surface as a 500 the client cannot interpret."""
        assert await store.get_credentials("nobody") is None

    async def test_access_tokens_are_keyed_and_stored_by_hash(self, store):
        """A database read should not yield a working credential."""
        token_hash = "a" * 64
        await store.put_access_token(
            token_hash,
            AccessToken(token=token_hash, client_id="client-1", scopes=[]),
        )

        stored = await store.get_access_token(token_hash)
        assert stored.token == token_hash

    async def test_deleting_a_token_revokes_it(self, store):
        """This is what opaque tokens buy over JWTs."""
        token_hash = "b" * 64
        await store.put_access_token(
            token_hash, AccessToken(token=token_hash, client_id="c", scopes=[])
        )
        await store.delete_access_token(token_hash)

        assert await store.get_access_token(token_hash) is None


class TestClients:
    async def test_registered_clients_round_trip(self, store):
        client = OAuthClientInformationFull(
            client_id="dynamic-1", redirect_uris=["http://localhost:54321/callback"]
        )
        await store.put_client(client)

        assert (await store.get_client("dynamic-1")).client_id == "dynamic-1"

    async def test_unknown_client_returns_none(self, store):
        assert await store.get_client("never-registered") is None


class TestRevocation:
    """Revocation is the reason this project issues opaque tokens rather than
    signed ones: load_access_token reads the store on every request, so removing
    a record takes effect immediately."""

    async def test_a_revoked_token_stops_resolving(self, store):
        from app.config import get_settings
        from app.oauth.provider import GoogleDriveAuthProvider

        provider = GoogleDriveAuthProvider(store, get_settings())
        raw = "raw-token-value"
        token_hash = __import__("hashlib").sha256(raw.encode()).hexdigest()
        await store.put_access_token(
            token_hash,
            AccessToken(token=token_hash, client_id="c1", scopes=[], subject="sub-1"),
        )

        assert await provider.load_access_token(raw) is not None

        await provider.revoke_token(
            AccessToken(token=raw, client_id="c1", scopes=[], subject="sub-1")
        )

        assert await provider.load_access_token(raw) is None

    async def test_revoking_leaves_google_credentials_alone(self, store):
        """Reconnecting should not require consenting at Google again."""
        from app.config import get_settings
        from app.oauth.provider import GoogleDriveAuthProvider

        provider = GoogleDriveAuthProvider(store, get_settings())
        await store.put_credentials(
            "sub-1",
            GoogleCredentials(
                access_token="a",
                refresh_token="r",
                expires_at=datetime.datetime.now(datetime.UTC),
                scopes=["drive.readonly"],
            ),
        )
        raw = "another-token"
        token_hash = __import__("hashlib").sha256(raw.encode()).hexdigest()
        await store.put_access_token(
            token_hash,
            AccessToken(token=token_hash, client_id="c1", scopes=[], subject="sub-1"),
        )

        await provider.revoke_token(
            AccessToken(token=raw, client_id="c1", scopes=[], subject="sub-1")
        )

        assert await store.get_credentials("sub-1") is not None

    async def test_revoking_an_unknown_token_is_a_no_op(self, store):
        """RFC 7009: revoking an invalid or already-revoked token is not an error."""
        from app.config import get_settings
        from app.oauth.provider import GoogleDriveAuthProvider

        provider = GoogleDriveAuthProvider(store, get_settings())
        await provider.revoke_token(
            AccessToken(token="never-issued", client_id="c1", scopes=[])
        )
