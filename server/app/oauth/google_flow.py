"""Leg 2: this server acting as an OAuth client against Google.

Knows nothing about Claude Code, MCP, or our own token format. Its whole
job is to turn a Google authorization code into stored credentials, and to
keep those credentials fresh.

Throughout this module "access token" means GOOGLE's. The tokens we issue
to MCP clients are handled in provider.py and never appear here.
"""
import datetime
import logging
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from ..config import get_settings
from ..errors import InvalidGrant, NotAuthorized, OAuthFlowError
from ..storage.base import GoogleCredentials, get_token_store

logger = logging.getLogger(__name__)


def build_google_consent_url(state: str) -> str:
    settings = get_settings()
    request_params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join([
            "https://www.googleapis.com/auth/drive.readonly",
            "openid",
            "email",
        ]),
        # Without access_type=offline Google issues NO refresh token, and the
        # integration silently stops working one hour after authorisation.
        "access_type": "offline",
        # Google omits the refresh token on re-authorisation unless forced.
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }

    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(request_params)}"

async def exchange_code(code: str) -> tuple[GoogleCredentials, str]:
    settings = get_settings()

    token_endpoint = "https://oauth2.googleapis.com/token"
    request_params = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret.get_secret_value(),
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }

    logger.info("Exchanging authorization code at Google's token endpoint")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_endpoint, data=request_params)
    except httpx.RequestError as exc:
        logger.error("Could not reach Google's token endpoint: %s", exc)
        raise OAuthFlowError(
            "Could not reach Google to complete sign-in. Please try again."
        ) from exc

    try:
        token_data = response.json()
    except ValueError as exc:
        logger.error(
            "Google returned a non-JSON response (HTTP %s)", response.status_code
        )
        raise OAuthFlowError(
            "Google returned an unreadable response during sign-in."
        ) from exc

    if response.status_code != 200:
        error = token_data.get("error", "unknown_error")
        description = token_data.get("error_description", "")
        logger.warning(
            "Google rejected the code exchange: %s (%s)", error, description
        )
        raise InvalidGrant(
            f"Google rejected the authorization code ({error})."
            + (f" {description}" if description else "")
        )

    if "id_token" not in token_data:
        logger.error("Token response contained no id_token; cannot identify user")
        raise OAuthFlowError(
            "Google's response did not identify the user. Please try again."
        )

    try:
        info = google_id_token.verify_oauth2_token(
            token_data["id_token"],
            google_requests.Request(),
            audience=settings.google_client_id,
        )
    except ValueError as exc:
        logger.error("id_token verification failed: %s", exc)
        raise OAuthFlowError(
            "Could not verify Google's identity response."
        ) from exc

    user_id = info["sub"]

    creds = GoogleCredentials(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=token_data["expires_in"]),
        scopes=token_data.get("scope", "").split(),
    )

    logger.info(
        "Code exchange succeeded for subject %s (refresh_token: %s)",
        user_id,
        "received" if creds.refresh_token else "ABSENT",
    )

    return creds, user_id


async def refresh_if_needed(user_id: str, creds: "GoogleCredentials") -> "GoogleCredentials":
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    if creds.expires_at - now > datetime.timedelta(seconds=60):
        return creds

    token_endpoint = "https://oauth2.googleapis.com/token"
    request_params = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret.get_secret_value(),
        "refresh_token": creds.refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        logger.info("Refreshing access token for user %s", user_id)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_endpoint, data=request_params)
            token_data = response.json()

            if response.status_code != 200:
                error = token_data.get("error", "unknown_error")
                description = token_data.get("error_description", "")

                if error == "invalid_grant":
                    logger.warning(
                        "Refresh token no longer valid for subject %s: %s",
                        user_id,
                        description,
                    )
                    raise NotAuthorized(
                        "Your Google authorisation is no longer valid. "
                        "Please sign in again to reconnect Google Drive."
                    )
                logger.error(
                    "Unexpected error refreshing token for subject %s: %s (%s)",
                    user_id,
                    error,
                    description,
                )
                raise OAuthFlowError(
                    f"Could not refresh Google access ({error})."
                    + (f" {description}" if description else "")
                )

            new_creds = GoogleCredentials(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token") or creds.refresh_token,
                expires_at=datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=token_data["expires_in"]),
                scopes=token_data.get("scope", "").split() or creds.scopes,
            )

            await get_token_store().put_credentials(user_id, new_creds)

            logger.info("Refreshed access token for subject %s", user_id)
            return new_creds
    except httpx.RequestError as exc:
        logger.error("Could not reach Google's token endpoint for refresh: %s", exc)
        raise OAuthFlowError(
            "Could not reach Google to refresh the access token. Please try again."
        ) from exc
