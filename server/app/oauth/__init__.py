"""OAuth 2.1 authorization-server implementation.

Read provider.py -> routes.py -> google_flow.py in that order.

DIVISION OF LABOUR: the mcp SDK (mcp.server.auth) owns the OAuth protocol —
metadata documents, dynamic client registration, /authorize, /token, PKCE
S256 verification, bearer middleware. This package owns the policy: who the
user authenticates as (Google) and where state lives.

THE CENTRAL IDEA — there are two OAuth handshakes, and this package is the
hinge between them:

    Claude Code  --(1)-->  this server        (we are the Authorization Server)
    this server  --(2)-->  Google             (we are the OAuth client)

Claude Code never sees a Google token. It receives a token that is only
meaningful to us; we exchange it internally for the user's Google credentials.
Google's refresh token never leaves this process boundary.

Do not let the two vocabularies bleed together. In this package,
"access token" without qualification means OURS. Google's are always
referred to explicitly as google_access_token / google_refresh_token.
"""
