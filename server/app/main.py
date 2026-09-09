"""Application entrypoint. Assembly only — no business logic lives here.

Two things here are load-bearing and easy to get wrong:

* Mounting an ASGI app does not run its lifespan. The MCP session manager has
  to be started explicitly, or the failure appears on the first /mcp request
  and reads as a protocol bug rather than a wiring one.
* The OAuth discovery documents must be served from the origin root, so the
  MCP app is mounted at "/" and every route of our own is registered before
  it — a root mount swallows whatever was not matched earlier.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .mcp_server.server import build_mcp_app

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

mcp_app = build_mcp_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Mounting an ASGI app does NOT run its lifespan. Without this the session
    # manager never starts and the failure appears on the first /mcp request,
    # looking like a protocol bug rather than a wiring one.
    async with mcp_app.router.lifespan_context(_):
        yield


app = FastAPI(title="OakMega Drive MCP", lifespan=lifespan)


@app.get("/status")
async def status():
    """Unauthenticated liveness check with no dependency on Google or Firestore.

    NOT /healthz: Cloud Run's Google Frontend reserves that path and answers it
    with its own 404 before the request reaches the container. The route works
    locally and disappears once deployed, which is a confusing way to lose an
    afternoon.
    """
    return {"status": "ok"}


# Mounted last and at the root: the protected-resource document must live at
# /.well-known/oauth-protected-resource/mcp, and a root mount swallows every
# path that was not registered before it.
app.mount("/", mcp_app)
