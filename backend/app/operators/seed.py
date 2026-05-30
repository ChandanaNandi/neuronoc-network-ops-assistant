"""Idempotent operator seed CLI.

Local dev / e2e seed for the `operators` table. Phase 23 adds the
optional `--password` flag so an operator can be created with a usable
password_hash (PBKDF2-SHA256 via stdlib `hashlib`). Idempotent by
`display_name`; re-running with --role or --password updates the row.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.auth.hashing import hash_password
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
        help=(
            "Operator role. As of Phase 23, only `admin` can "
            "approve/reject remediation plans."
        ),
    )
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Optional password (PBKDF2-SHA256 hashed at persist time). "
            "If omitted, the operator row is created without a password "
            "and cannot log in until one is set. Re-running with "
            "--password rotates the hash on the existing row."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    new_hash = hash_password(args.password) if args.password else None

    with SessionLocal() as db:
        existing = db.scalar(
            select(Operator).where(Operator.display_name == args.name)
        )
        if existing is not None:
            changes: list[str] = []
            if existing.role != args.role:
                existing.role = args.role
                changes.append(f"role->{args.role}")
            if new_hash is not None:
                existing.password_hash = new_hash
                changes.append("password set")
            if changes:
                db.commit()
                db.refresh(existing)
                print(
                    f"[operators] '{args.name}' updated "
                    f"({', '.join(changes)}) (id={existing.id})"
                )
            else:
                print(
                    f"[operators] '{args.name}' already exists "
                    f"(id={existing.id}, role={existing.role})"
                )
        else:
            op = Operator(
                display_name=args.name,
                role=args.role,
                password_hash=new_hash,
            )
            db.add(op)
            db.commit()
            db.refresh(op)
            has_pw = " with password" if new_hash else ""
            print(
                f"[operators] created '{op.display_name}'{has_pw} "
                f"(id={op.id}, role={op.role})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
