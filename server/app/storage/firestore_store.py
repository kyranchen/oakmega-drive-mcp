"""Firestore-backed TokenStore. The deployed backend.

Uses Application Default Credentials — on Cloud Run that is the service
account attached to the service, so there is no key file anywhere.

Suggested collection layout (one document per record, id = the key):
    credentials/{google_sub}
    clients/{client_id}
    pending_auth/{state}
    auth_codes/{code}
    access_tokens/{token_hash}

Two things to get right:

1. pop_pending / pop_code must be atomic. Use a Firestore transaction that
   reads and deletes in one commit, so a replayed authorization code cannot
   be redeemed twice.

2. Short-lived collections need cleanup. Firestore has a native TTL policy
   feature — set it on an `expires_at` field for pending_auth, auth_codes,
   and grants, and Google expires the documents for you. Cheaper and more
   honest than a cron job. Note TTL deletion is best-effort within ~24h, so
   still validate expiry on read; the policy is for garbage collection, not
   for security.

Store only a HASH of issued access tokens (see AccessGrant).
"""


class FirestoreTokenStore:
    """See storage.base.TokenStore for the contract."""

    def __init__(self, project_id: str, database: str) -> None:
        raise NotImplementedError
