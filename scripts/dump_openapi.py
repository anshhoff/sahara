#!/usr/bin/env python3
"""Write the FastAPI OpenAPI schema to a file (task 5.2).

The typed client in `web/lib/api.ts` is generated from this, so a route rename or a
changed response shape breaks the frontend BUILD rather than the page. That is the
whole point of the step: the API is already the contract, and this makes the contract
machine-checkable instead of a convention two codebases agree to remember.

    python scripts/dump_openapi.py --out web/lib/openapi.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import with a throwaway database path: generating a schema must never touch, create
# or migrate the real one.
os.environ.setdefault("DB_PATH", str(Path(__file__).resolve().parent.parent / ".openapi-scratch.db"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="web/lib/openapi.json")
    args = ap.parse_args()

    from app.main import app

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    n = sum(len(v) for v in schema.get("paths", {}).values())
    print(f"wrote {out} — {len(schema.get('paths', {}))} paths, {n} operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
