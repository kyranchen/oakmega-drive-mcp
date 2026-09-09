"""The MCP server instance and its ASGI app.

Passing `auth` is what makes the SDK mount the OAuth discovery documents,
/register, /authorize and /token, and install the bearer middleware that
returns the WWW-Authenticate header clients follow. None of it can be attached
after construction — an instance built without `auth` serves /mcp alone.

stateless_http=True because Cloud Run spreads requests across instances with
no session affinity: the default mode keeps per-session state in memory and
hands the client a session id, which the next request may present to a
container that has never seen it. The cost is no server-initiated
notifications, which this server does not use.
"""

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer

from ..config import get_settings
from ..oauth.provider import GoogleDriveAuthProvider
from ..storage.base import get_token_store

settings = get_settings()

# Passing `auth` is what makes the SDK mount the discovery documents,
# /register, /authorize and /token, and install the bearer middleware that
# emits the WWW-Authenticate header Claude Code follows. None of it can be
# attached after construction.
mcp = MCPServer(
    "google-drive-reader",
    auth_server_provider=GoogleDriveAuthProvider(get_token_store(), settings),
    auth=AuthSettings(
        # This server is the issuer. Google appears nowhere in this block —
        # Claude Code authenticates against us, and we deal with Google
        # separately in oauth/google_flow.py.
        issuer_url=settings.public_base_url,
        resource_server_url=settings.resource_identifier,
        # RFC 8707: refuse a token that was issued for a different resource.
        validate_token_resource=True,
        # Required. Claude Code's redirect URI is a localhost callback on a
        # port not known until runtime, so it cannot be pre-registered.
        client_registration_options=ClientRegistrationOptions(enabled=True),
    ),
)


def build_mcp_app():
    """ASGI app for mounting. Stateless because Cloud Run has no session affinity."""
    return mcp.streamable_http_app(stateless_http=True)
