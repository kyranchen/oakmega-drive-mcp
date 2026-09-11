"""The one OAuth route the SDK does not provide: Google's callback.

Everything else — /authorize, /token, /register and the discovery documents —
is mounted by the SDK. This exists because leg 2 (us to Google) is outside the
MCP spec entirely, so the SDK has no opinion about it.

This handler is the hinge between the two handshakes, and its ordering
matters: credentials are persisted before the authorization code is minted,
because a code redeemed for a user whose Google tokens were never saved
produces tool calls that fail with no visible cause.
"""

import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, RedirectResponse
from mcp.server.auth.provider import AuthorizationCode

from ..storage.base import (
    AUTH_CODE_TTL_SECONDS,
    PendingAuthorization,
    get_token_store,
)
from .google_flow import exchange_code

router = APIRouter()


@router.get("/oauth/google/callback")
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    store = get_token_store()

    pending = await store.pop_pending(state)
    if not pending:
        return PlainTextResponse(
            "Authorization request expired or invalid. Please start again.",
            status_code=400,
        )

    if error:
        return RedirectResponse(
            build_redirect_uri(pending, error=error), status_code=302
        )

    creds, google_sub = await exchange_code(code)

    await store.put_credentials(google_sub, creds)

    our_code = secrets.token_urlsafe(32)

    await store.put_code(
        AuthorizationCode(
            subject=google_sub,
            client_id=pending.client_id,
            code_challenge=pending.code_challenge,
            redirect_uri=pending.redirect_uri,
            redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            resource=pending.resource,
            code=our_code,
            scopes=pending.scopes,
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
        )
    )

    return RedirectResponse(build_redirect_uri(pending, code=our_code), status_code=302)


def build_redirect_uri(
    pending: PendingAuthorization,
    *,
    code: str | None = None,
    error: str | None = None,
) -> str:
    query: dict[str, str] = {}
    if code is not None:
        query["code"] = code
    if error is not None:
        query["error"] = error
    if pending.client_state is not None:
        query["state"] = pending.client_state

    separator = "&" if "?" in pending.redirect_uri else "?"
    return f"{pending.redirect_uri}{separator}{urlencode(query)}"
