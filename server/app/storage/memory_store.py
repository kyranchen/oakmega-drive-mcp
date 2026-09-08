"""In-process TokenStore. Local development only.

Explicitly NOT safe for Cloud Run: state dies with the container and is not
shared between instances. Its job is to let you run and test the whole auth
chain locally without provisioning any GCP resources — and to prove the
Protocol abstraction is real rather than decorative.

Implement as a class holding five dicts, one per namespace, with an asyncio
lock around the pop_* operations to preserve the single-use guarantee.
TTL expiry can be lazy (check created_at on read) rather than a sweeper.
"""


import asyncio
import datetime
import time

from mcp.server.auth.provider import AccessToken, AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull

from .base import GoogleCredentials, PendingAuthorization


class MemoryTokenStore:
    """See storage.base.TokenStore for the contract."""

    def __init__(self) -> None:
        self._credentials: dict[str, GoogleCredentials] = {}
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending: dict[str, PendingAuthorization] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_credentials(self, subject: str) -> GoogleCredentials | None:
        return self._credentials.get(subject)

    async def put_credentials(self, subject: str, creds: GoogleCredentials) -> None:
        self._credentials[subject] = creds

    async def put_client(self, client: OAuthClientInformationFull) -> None:
        self._clients[client.client_id] = client

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def put_pending(self, state: str, pending: PendingAuthorization) -> None:
        self._pending[state] = pending

    async def pop_pending(self, state: str) -> PendingAuthorization | None:
        async with self._lock:
            record = self._pending.pop(state, None)
            if record is None:
                return None
            if record.created_at + datetime.timedelta(minutes=10) < datetime.datetime.now(tz=datetime.timezone.utc):
                return None
            return record

    async def put_code(self, record: AuthorizationCode) -> None:
        self._codes[record.code] = record

    async def pop_code(self, code: str) -> AuthorizationCode | None:
        async with self._lock:
            record = self._codes.pop(code, None)
            if record is None:
                return None
            if record.expires_at < time.time():
                return None
            return record

    async def put_access_token(self, token_hash: str, token: AccessToken) -> None:
        self._access_tokens[token_hash] = token

    async def get_access_token(self, token_hash: str) -> AccessToken | None:
        return self._access_tokens.get(token_hash)

    async def delete_access_token(self, token_hash: str) -> None:
        self._access_tokens.pop(token_hash, None)