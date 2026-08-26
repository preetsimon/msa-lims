"""Verify the audit trail's hash chain from the command line.

``make verify-chain`` (or ``python -m msa_lims.db.verify_chain``) runs
:func:`msa_lims.db.audit.verify_chain` against the deployment's own
database and reports the result — the standalone-script half of audit idea
#1's own sketch, alongside ``GET /api/audit/verify`` for the identical check
over HTTP. Exits non-zero on a broken chain so it is safe to wire into a
scheduled job or a pre-release check.
"""

from __future__ import annotations

import argparse
import sys

from msa_lims.db.audit import verify_chain
from msa_lims.db.session import session_scope


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upto",
        type=int,
        default=None,
        help="Verify only up to this audit_event id (default: the whole table).",
    )
    args = parser.parse_args()

    with session_scope() as session:
        result = verify_chain(session, upto=args.upto)

    if result.valid:
        print(
            f"chain verifies: {result.verified_count} row(s), head {result.head_hash or '(empty)'}"
        )
        return

    assert result.first_break is not None
    print(
        f"CHAIN BROKEN at audit_event.id={result.first_break.id}: {result.first_break.reason}",
        file=sys.stderr,
    )
    print(f"{result.verified_count} row(s) verified before the break.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
