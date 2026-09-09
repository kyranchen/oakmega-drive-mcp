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

import logging
import secrets
from datetime import datetime

from mcp.server.auth.provider import OAuthAuthorizationServerProvider

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
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        await self.store.put_pending(state, pending)
        return build_google_consent_url(state)

    async def load_authorization_code(self, client, authorization_code):
        raise NotImplementedError

    async def exchange_authorization_code(self, client, authorization_code):
        raise NotImplementedError

    async def load_access_token(self, token):
        raise NotImplementedError

    async def load_refresh_token(self, client, refresh_token):
        # `load_*` reports "not found" by returning None; raising here would
        # surface as a 500 instead of a well-formed OAuth error.
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        # Deliberately unsupported: the credential lifetime that matters is
        # Google's refresh token, which this server holds and rotates itself.
        raise NotImplementedError("Refresh token grant is not supported")

    async def revoke_token(self, token) -> None:
        raise NotImplementedError

    async def exchange_identity_assertion(self, client, params):
        raise NotImplementedError("Identity assertion is not supported")
