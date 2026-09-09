#!/usr/bin/env python
"""Dump what the token store currently holds. Development aid, not app code.

    python scripts/inspect_firestore.py              # all collections
    python scripts/inspect_firestore.py credentials  # one collection

Secrets are masked: this prints enough to see that a record exists and is
shaped correctly, without putting a usable token on your screen.
"""

import sys

from google.cloud import firestore

PROJECT = "oakmega-take-home"
COLLECTIONS = ["credentials", "clients", "pending_auth", "auth_codes", "access_tokens"]
SENSITIVE = {"access_token", "refresh_token", "client_secret", "token"}


def mask(key: str, value: object) -> object:
    if key in SENSITIVE and isinstance(value, str) and value:
        return f"<{len(value)} chars: {value[:6]}…>"
    return value


def main() -> None:
    db = firestore.Client(project=PROJECT)
    names = sys.argv[1:] or COLLECTIONS

    for name in names:
        docs = list(db.collection(name).stream())
        print(f"\n=== {name}  ({len(docs)} 筆) ===")
        for doc in docs:
            print(f"  {doc.id}")
            for k, v in sorted(doc.to_dict().items()):
                print(f"      {k:34} {mask(k, v)}")


if __name__ == "__main__":
    main()
