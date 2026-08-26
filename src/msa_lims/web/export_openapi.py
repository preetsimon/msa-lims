"""Dump the live app's OpenAPI schema to a file — no running server needed.

``openapi-typescript`` (``frontend/``) generates
``frontend/src/generated-types.ts`` from this file's output; a CI step
regenerates both and diffs the result against what is committed, so a route
or schema change that forgot to regenerate types fails loudly instead of
leaving the frontend's hand-kept guess free to drift silently — the standing
debt audit idea #18 named.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from msa_lims.web.app import create_app


def export_openapi_schema(output_path: Path) -> None:
    schema = create_app().openapi()
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=Path("openapi.json"),
        help="Where to write the schema (default: ./openapi.json)",
    )
    args = parser.parse_args()
    export_openapi_schema(args.output)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
