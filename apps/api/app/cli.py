"""Operator commands. Usage (inside the api container):

    python -m app.cli create-admin you@example.com [--org "Name"]

Creates a platform admin with a random password, written to stdout once. Nothing is stored
in plain text; change it after first login if you like (re-run with a new email to add more).
"""

import argparse
import secrets
import sys

from app.db import SessionLocal
from app.services import accounts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("email")
    c.add_argument("--org", default=None)
    args = p.parse_args(argv)

    if args.cmd == "create-admin":
        password = secrets.token_urlsafe(18)
        with SessionLocal() as db:
            try:
                accounts.register(db, args.email, password, None, args.org, is_admin=True)
            except accounts.AuthError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        print(f"admin created: {args.email}")
        print(f"password: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
