"""The storage contract, plus the record shapes that cross it.

WHY THIS LAYER EXISTS (worth being able to defend in the interview):
Cloud Run runs the MCP server statelessly across multiple instances with no
session affinity. Three separate HTTP requests in the auth chain — /authorize,
Google's callback, /token — can each land on a different container. So every
piece of cross-request state must live outside process memory. That is a
correctness requirement of the platform, not gold-plating.

Note most record types come FROM THE SDK rather than being defined here.
The provider interface speaks in those types, so storing anything else means
converting on every read and write. Only two shapes are genuinely ours:
GoogleCredentials (the SDK knows nothing about Google) and
PendingAuthorization (the SDK knows nothing about a detour to a third party).

Why a Protocol rather than a base class: the two backends share no code, only
a shape. Typing it structurally keeps memory_store a plain dict wrapper.
"""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Protocol

from mcp.server.auth.provider import AccessToken, AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull

from ..config import get_settings


@dataclass
class GoogleCredentials:
    """Google's tokens for one authenticated end user. Keyed by Google `sub`.

    The refresh_token is the crown jewel: it never leaves this server and is
    never handed to the MCP client. Google only returns it on the FIRST
    consent unless access_type=offline + prompt=consent are sent, so a
    re-authorisation may legitimately arrive with refresh_token=None and must
    not clobber a previously stored one.
    """

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]


@dataclass
class PendingAuthorization:
    """An /authorize request parked while the user is away at Google's consent screen.

    Keyed by the opaque `state` we send Google. Holds what the callback needs
    to resume the MCP-side handshake.

    client_state is the CLIENT's state value, echoed back untouched on the
    final redirect. It is NOT the key of this record — that is our own,
    separate state. Keeping the two straight is the main trap in this flow.

    Short-lived: give it a ~10 minute TTL and treat absence as failure.
    """

    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    client_state: str | None
    code_challenge: str
    scopes: list[str]
    resource: str | None
    created_at: datetime


class TokenStore(Protocol):
    """Everything the auth chain needs to persist.

    All methods async so the Firestore backend can use the async client
    without the memory backend needing a thread pool.
    """

    # --- End-user Google credentials (ours) ------------------------------
    async def get_credentials(self, subject: str) -> GoogleCredentials | None: ...
    async def put_credentials(self, subject: str, creds: GoogleCredentials) -> None: ...

    # --- Registered MCP clients (SDK type) -------------------------------
    async def put_client(self, client: OAuthClientInformationFull) -> None: ...
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None: ...

    # --- Pending /authorize, keyed by our Google `state` (ours) ----------
    async def put_pending(self, state: str, pending: PendingAuthorization) -> None: ...
    async def pop_pending(self, state: str) -> PendingAuthorization | None: ...

    # --- Authorization codes we minted (SDK type) ------------------------
    async def get_code(self, code: str) -> AuthorizationCode | None: ...
    async def put_code(self, record: AuthorizationCode) -> None: ...
    async def pop_code(self, code: str) -> AuthorizationCode | None: ...

    # --- Access tokens we issued (SDK type), keyed by token HASH ---------
    async def put_access_token(self, token_hash: str, token: AccessToken) -> None: ...
    async def get_access_token(self, token_hash: str) -> AccessToken | None: ...
    async def delete_access_token(self, token_hash: str) -> None: ...

    # NOTE on pop_* semantics: these must be atomic read-and-delete, not
    # read-then-delete. Two concurrent redemptions of the same code should
    # yield exactly one success. Firestore transactions give you this;
    # a naive get() followed by delete() does not.


@lru_cache
def get_token_store() -> TokenStore:
    """Construct the backend named by settings.token_store_backend.

    Cache process-wide — building a Firestore client per request is wasteful
    and leaks connections.
    """
    from .firestore_store import FirestoreTokenStore
    from .memory_store import MemoryTokenStore

    settings = get_settings()
    if settings.token_store_backend == "memory":
        return MemoryTokenStore()
    elif settings.token_store_backend == "firestore":
        return FirestoreTokenStore(
            project_id=settings.gcp_project_id,
            database=settings.firestore_database,
        )
    else:
        raise ValueError(
            f"Unknown token store backend {settings.token_store_backend!r}"
        )
