"""List the models the configured endpoint actually offers, and pick one.

    python -m parchi.models_cli                # every model, grouped
    python -m parchi.models_cli --filter glm   # just the GLM family
    python -m parchi.models_cli --pick         # choose one and write it to .env

Fetching the catalogue rather than shipping a hardcoded list is deliberate: the
endpoint's line-up changes, and a pinned name that quietly 404s would turn every
intent check into a degraded row - which still returns a verdict, so it would not
look broken.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import openai_provider


def _write_env(key: str, value: str, path: str = ".env") -> None:
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f]
    lines = [ln for ln in lines if not ln.startswith(key + "=")]
    lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="List models on the configured endpoint")
    ap.add_argument("--filter", default="", help="substring to match, e.g. glm")
    ap.add_argument("--refresh", action="store_true", help="bypass the process cache")
    ap.add_argument("--pick", action="store_true",
                    help="choose a model interactively and save it to .env")
    args = ap.parse_args()

    openai_provider.load_dotenv()
    try:
        models = openai_provider.list_models(refresh=args.refresh)
    except openai_provider.ProviderNotConfigured as exc:
        print(exc, file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(openai_provider.redact(exc), file=sys.stderr)
        return 1

    shown = [m for m in models if args.filter.lower() in m.lower()]
    print(f"endpoint : {openai_provider.base_url()}")
    print(f"models   : {len(models)} total"
          + (f", {len(shown)} matching {args.filter!r}" if args.filter else ""))
    try:
        print(f"default  : {openai_provider.resolve_model()}")
    except RuntimeError as exc:
        print(f"default  : unresolved ({exc})")
    print()

    for i, name in enumerate(shown, 1):
        print(f"  {i:3d}. {name}")

    if args.pick:
        if not shown:
            print("\nnothing to pick from", file=sys.stderr)
            return 1
        try:
            raw = input("\nnumber to use (blank to cancel): ").strip()
        except EOFError:
            return 1
        if not raw:
            return 0
        if not raw.isdigit() or not 1 <= int(raw) <= len(shown):
            print("not a listed number", file=sys.stderr)
            return 1
        chosen = shown[int(raw) - 1]
        _write_env("PARCHI_MODEL", chosen)
        print(f"\nPARCHI_MODEL={chosen} written to .env (gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
