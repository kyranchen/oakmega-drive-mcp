"""Firestore-backed TokenStore. The deployed backend.

Authenticates through Application Default Credentials — on Cloud Run that is
the service account attached to the service, so no key file exists anywhere in
this project.

    credentials/{google_sub}       Google's tokens. Long-lived, one per user.
    clients/{client_id}            Dynamically registered MCP clients.
    access_tokens/{token_hash}     Tokens we issued, keyed and stored by hash.
    pending_auth/{state}           An /authorize request parked mid-flow.
    auth_codes/{code}              An authorization code awaiting redemption.

The last two exist only to cross a request boundary and are deleted as they
are read, so both collections are empty in a healthy system.

Access tokens are stored hashed, never in the clear: a read of this database
should not yield working credentials.

pop_pending and pop_code use transactions because the read and the delete must
commit together. Two concurrent redemptions of one authorization code must
resolve to exactly one success, and Firestore's optimistic locking is what
provides that — the in-memory backend gets it free from the GIL.

Expiry is still checked on read. Firestore's native TTL policies would be the
right way to reclaim space, but their deletion is best-effort within ~24h, so
they are garbage collection rather than a correctness guarantee.
"""

import dataclasses
import time

from google.cloud import firestore
from google.cloud.firestore import AsyncClient
from mcp.server.auth.provider import AccessToken, AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull

from .base import GoogleCredentials, PendingAuthorization

PENDING_TTL_SECONDS = 600


class FirestoreTokenStore:
    """See storage.base.TokenStore for the contract."""

    def __init__(self, project_id: str, database: str) -> None:
        self.db = AsyncClient(project=project_id, database=database)

    async def get_credentials(self, subject: str) -> GoogleCredentials | None:
        doc = await self.db.collection("credentials").document(subject).get()
        if not doc.exists:
            return None
        return GoogleCredentials(**doc.to_dict())

    async def put_credentials(self, subject: str, creds: GoogleCredentials) -> None:
        await (
            self.db.collection("credentials")
            .document(subject)
            .set(dataclasses.asdict(creds))
        )

    async def put_client(self, client: OAuthClientInformationFull) -> None:
        await (
            self.db.collection("clients")
            .document(client.client_id)
            .set(client.model_dump(mode="json"))
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        doc = await self.db.collection("clients").document(client_id).get()
        if not doc.exists:
            return None
        return OAuthClientInformationFull.model_validate(doc.to_dict())

    async def put_pending(self, state: str, pending: PendingAuthorization) -> None:
        await (
            self.db.collection("pending_auth")
            .document(state)
            .set(dataclasses.asdict(pending))
        )

    async def pop_pending(self, state: str) -> PendingAuthorization | None:
        """Atomically read and delete a parked authorization request.

        The read and the delete have to commit together: two concurrent
        callbacks carrying the same state must not both succeed.
        """
        ref = self.db.collection("pending_auth").document(state)

        @firestore.async_transactional
        async def txn(transaction):
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            transaction.delete(ref)
            return snapshot.to_dict()

        data = await txn(self.db.transaction())
        if data is None:
            return None

        # Expiry is checked outside the transaction: a conflict re-runs the
        # whole decorated function, so it holds the read and the delete and
        # nothing else. An expired record is deleted either way — it is spent.
        record = PendingAuthorization(**data)
        if record.created_at.timestamp() + PENDING_TTL_SECONDS < time.time():
            return None
        return record

    async def put_code(self, record: AuthorizationCode) -> None:
        await (
            self.db.collection("auth_codes")
            .document(record.code)
            .set(record.model_dump(mode="json"))
        )

    async def get_code(self, code: str) -> AuthorizationCode | None:
        """Read without consuming. The SDK verifies PKCE against the record
        before calling exchange, so deletion belongs in pop_code."""
        doc = await self.db.collection("auth_codes").document(code).get()
        if not doc.exists:
            return None
        return AuthorizationCode.model_validate(doc.to_dict())

    async def pop_code(self, code: str) -> AuthorizationCode | None:
        """Atomically read and delete an authorization code. Single use."""
        ref = self.db.collection("auth_codes").document(code)

        @firestore.async_transactional
        async def txn(transaction):
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            transaction.delete(ref)
            return snapshot.to_dict()

        data = await txn(self.db.transaction())
        if data is None:
            return None

        record = AuthorizationCode.model_validate(data)
        if record.expires_at < time.time():
            return None
        return record

    async def put_access_token(self, token_hash: str, token: AccessToken) -> None:
        await (
            self.db.collection("access_tokens")
            .document(token_hash)
            .set(token.model_dump(mode="json"))
        )

    async def get_access_token(self, token_hash: str) -> AccessToken | None:
        doc = await self.db.collection("access_tokens").document(token_hash).get()
        if not doc.exists:
            return None
        return AccessToken.model_validate(doc.to_dict())

    async def delete_access_token(self, token_hash: str) -> None:
        await self.db.collection("access_tokens").document(token_hash).delete()
