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

from urllib.parse import urlparse

from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

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
        # RFC 7009. Mounts /revoke, which is the entry point that makes the
        # opaque-token choice worth its per-request lookup.
        revocation_options=RevocationOptions(enabled=True),
    ),
)


def build_mcp_app():
    """ASGI app for mounting. Stateless because Cloud Run has no session affinity."""
    # The SDK's DNS-rebinding protection is on by default and allows only
    # 127.0.0.1, so a deployed service rejects every request with 421 Invalid
    # Host header. Derive the allowed host from the configured public origin
    # rather than disabling the protection.
    return mcp.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlparse(settings.public_base_url).netloc],
            allowed_origins=[settings.public_base_url],
        ),
    )


# Imported for its side effect: the @mcp.tool() decorators in that module only
# run when it is imported, and nothing else imports it. Without this the server
# starts cleanly, authenticates correctly, and exposes no tools at all.
from . import tools  # noqa: F401  (import at end: tools imports `mcp` from here)
