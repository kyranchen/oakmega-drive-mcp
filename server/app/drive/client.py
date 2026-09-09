"""Builds an authorised Drive service for a given end user.

  get_drive_service(user_id) -> Resource
      Load GoogleCredentials from the token store, refresh if near expiry
      (delegating to oauth.google_flow), and return a googleapiclient
      Drive v3 service. Raise NotAuthorized when there are no credentials —
      that error is what tells the user to authorise, so it must not be
      swallowed into a generic failure.

Two practical notes:

1. googleapiclient is synchronous and blocking. Under an async FastAPI app,
   calling it directly stalls the event loop. Run it in a thread
   (asyncio.to_thread / starlette.concurrency.run_in_threadpool). With one
   reviewer clicking through two files this will never actually bite you, but
   it is exactly the kind of thing worth mentioning in ARCHITECTURE.md as a
   known trade-off rather than leaving it to be discovered.

2. The service object caches an HTTP connection. Building one per request is
   fine at this scale; sharing one across users is NOT — credentials are
   bound into it.
"""

import asyncio

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..config import get_settings
from ..errors import NotAuthorized
from ..oauth.google_flow import refresh_if_needed
from ..storage.base import get_token_store

TOKEN_URI = "https://oauth2.googleapis.com/token"

async def get_drive_service(subject: str):
    store = get_token_store()
    settings = get_settings()

    creds = await store.get_credentials(subject)
    if creds is None:
        raise NotAuthorized(f"No Google credentials stored for subject {subject}.")

    creds = await refresh_if_needed(subject, creds)

    google_creds = Credentials(
        token=creds.access_token,
        refresh_token=creds.refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        scopes=creds.scopes
    )

    return await asyncio.to_thread(
        build, "drive", "v3", credentials=google_creds, cache_discovery=False
    )


async def call(request):
    """Run a googleapiclient request off the event loop.

    googleapiclient is synchronous, so `request.execute()` blocks the whole
    event loop for the duration of a network round trip. Every Drive call in
    this package goes through here:

        files = await call(service.files().list(q=...))
    """
    return await asyncio.to_thread(request.execute)
