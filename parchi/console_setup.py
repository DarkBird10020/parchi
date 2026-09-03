"""Set up the operations console account.

    python -m parchi.console_setup

Asks for an email and a password, prints the two lines to put in `.env`, and
never writes the password anywhere. `.env` is gitignored, so the hash stays on
the machine that made it and the password stays nowhere at all.

Run with --write to append straight to .env instead of copying by hand.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from .operators import hash_password


def main() -> int:
    ap = argparse.ArgumentParser(description="Create the console operator account")
    ap.add_argument("--email", default="")
    ap.add_argument("--write", action="store_true",
                    help="append the settings to .env instead of printing them")
    ap.add_argument("--env-path", default=".env")
    args = ap.parse_args()

    email = args.email or input("operator email: ").strip()
    if not email or "@" not in email:
        print("that does not look like an email address", file=sys.stderr)
        return 2

    # getpass, so the password is not echoed and does not land in shell history.
    password = getpass.getpass("password: ")
    if len(password) < 8:
        print("use at least 8 characters", file=sys.stderr)
        return 2
    if password != getpass.getpass("password again: "):
        print("those did not match", file=sys.stderr)
        return 2

    encoded = hash_password(password)
    lines = [f"PARCHI_CONSOLE_EMAIL={email}",
             f"PARCHI_CONSOLE_PASSWORD_HASH={encoded}"]

    if args.write:
        existing = ""
        if os.path.exists(args.env_path):
            with open(args.env_path, encoding="utf-8") as f:
                existing = f.read()
        keep = [ln for ln in existing.splitlines()
                if not ln.startswith(("PARCHI_CONSOLE_EMAIL=",
                                      "PARCHI_CONSOLE_PASSWORD_HASH="))]
        with open(args.env_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(keep + lines) + "\n")
        print(f"\nwritten to {args.env_path}, which is gitignored.")
    else:
        print("\nPut these two lines in .env (gitignored):\n")
        for line in lines:
            print("  " + line)

    print("\nThe password itself is stored nowhere. Only this scrypt hash is,")
    print("and it cannot be turned back into the password.")
    print("Restart the server, then open /console.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
