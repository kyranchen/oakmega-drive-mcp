"""The provider the MCP SDK calls back into.

Control is inverted here. The SDK owns the OAuth protocol — parameter
validation, PKCE, error shapes — and calls these methods whenever it needs a
decision only this application can make: whether a client is known, where the
user should go to sign in, what token to issue, whose data a token represents.

This is also where the two handshakes meet:

    Claude Code  --(1)-->  this server  --(2)-->  Google

authorize() ends leg 1 by parking the request and returning Google's consent
URL. Leg 2 resumes in routes.py, which mints the authorization code that
exchange_authorization_code() later redeems. `subject` is the thread running
through all of it: set from Google's `sub` when the code is minted, carried
onto the access token, and read back to decide whose Drive a tool call opens.

Still to implement: load_authorization_code (read-only — the SDK verifies PKCE
against the record before calling exchange, so deletion belongs in exchange,
not here), exchange_authorization_code (single-use redemption, mint a token,
store only its hash), and load_access_token (hash the presented token, look it
up, return None rather than raising on any failure).
"""

import hashlib
import logging
import secrets
import time
from datetime import UTC, datetime

from mcp.server.auth.provider import (
    AccessToken,
    OAuthAuthorizationServerProvider,
    TokenError,
)
from mcp.shared.auth import OAuthToken

from ..storage.base import PendingAuthorization
from .google_flow import build_google_consent_url

logger = logging.getLogger(__name__)


class GoogleDriveAuthProvider(OAuthAuthorizationServerProvider):
    """Bridges the two OAuth handshakes.

    The SDK owns the protocol and calls these methods whenever it needs a
    decision that only this application can make: is this client known, where
    should the user go to sign in, what token should we issue, who does this
    token represent.
    """

    def __init__(self, store, settings) -> None:
        self.store = store
        self.settings = settings

    # --- Client registration -------------------------------------------
    async def get_client(self, client_id):
        return await self.store.get_client(client_id)

    async def register_client(self, client_info):
        await self.store.put_client(client_info)

    async def authorize(self, client, params) -> str:
        state = secrets.token_urlsafe(32)
        pending = PendingAuthorization(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            client_state=params.state,
            code_challenge=params.code_challenge,
            scopes=params.scopes or [],
            resource=params.resource,
            created_at=datetime.now(UTC),
        )
        await self.store.put_pending(state, pending)
        return build_google_consent_url(state)

    async def load_authorization_code(self, client, authorization_code):
        record = await self.store.get_code(authorization_code)
        if record is None:
            return None
        if record.client_id != client.client_id:
            return None
        return record

    async def exchange_authorization_code(self, client, authorization_code):
        record = await self.store.pop_code(authorization_code.code)
        if record is None:
            raise TokenError(
                "invalid_grant", "authorization code has already been used"
            )

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_object = AccessToken(
            token=token_hash,
            client_id=record.client_id,
            scopes=record.scopes,
            resource=record.resource,
            subject=record.subject,
            expires_at=int(time.time()) + 3600,
        )

        await self.store.put_access_token(token_hash, token_object)

        return OAuthToken(
            access_token=raw_token, token_type="Bearer", expires_in=24 * 3600
        )

    async def load_access_token(self, token):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        record = await self.store.get_access_token(token_hash)
        if record is None:
            return None
        if record.expires_at and record.expires_at < time.time():
            return None
        return record

    async def load_refresh_token(self, client, refresh_token):
        # `load_*` reports "not found" by returning None; raising here would
        # surface as a 500 instead of a well-formed OAuth error.
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        # Deliberately unsupported: the credential lifetime that matters is
        # Google's refresh token, which this server holds and rotates itself.
        raise NotImplementedError("Refresh token grant is not supported")

    async def revoke_token(self, token) -> None:
        """Delete an issued token so the next request carrying it fails.

        This is what the opaque-token choice buys. load_access_token reads the
        store on every request, so removing the record takes effect
        immediately — a signed token could not be withdrawn before it expired.

        Only the token we issued is revoked. The user's Google authorisation is
        untouched, so reconnecting does not require consenting again.

        Per the spec, revoking an unknown or already-revoked token is a no-op
        rather than an error.
        """
        token_hash = hashlib.sha256(token.token.encode()).hexdigest()
        await self.store.delete_access_token(token_hash)
        logger.info("Revoked an access token for subject %s", token.subject)

    async def exchange_identity_assertion(self, client, params):
        raise NotImplementedError("Identity assertion is not supported")
