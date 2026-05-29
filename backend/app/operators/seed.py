"""Idempotent operator seed CLI.

NOT production auth: this creates a row in the `operators` table for the
local dev / e2e flow. No password, no token, no session. Safe to run
repeatedly - matches an existing row by `display_name`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.db.models import Operator, OperatorRole
from app.db.session import SessionLocal


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.operators.seed",
        description=(
            "Create or update a local dev operator. Idempotent by --name. "
            "No password, no token - this is the Phase 13A minimal local "
            "identity row, NOT a production auth subject."
        ),
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Operator display name (unique).",
    )
    parser.add_argument(
        "--role",
        default=OperatorRole.operator.value,
        choices=[role.value for role in OperatorRole],
        help="Operator role (advisory only - no RBAC is enforced).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        existing = db.scalar(
            select(Operator).where(Operator.display_name == args.name)
        )
        if existing is not None:
            # Keep idempotent: if role differs, update it.
            if existing.role != args.role:
                existing.role = args.role
                db.commit()
                db.refresh(existing)
                print(
                    f"[operators] '{args.name}' updated role -> {args.role} "
                    f"(id={existing.id})"
                )
            else:
                print(
                    f"[operators] '{args.name}' already exists "
                    f"(id={existing.id}, role={existing.role})"
                )
        else:
            op = Operator(display_name=args.name, role=args.role)
            db.add(op)
            db.commit()
            db.refresh(op)
            print(
                f"[operators] created '{op.display_name}' "
                f"(id={op.id}, role={op.role})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
