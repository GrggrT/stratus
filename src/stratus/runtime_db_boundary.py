"""Fail closed when the database runtime-role boundary has drifted."""

from __future__ import annotations

import asyncio
import sys

from stratus.config import get_settings
from stratus.db.session import create_engine, verify_runtime_engine


async def assert_runtime_database_boundary() -> None:
    engine = create_engine(get_settings())
    try:
        await verify_runtime_engine(engine)
    finally:
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(assert_runtime_database_boundary())
    except Exception as exc:
        # Do not render exception details: driver errors can include connection
        # material. Operators can diagnose with the owner-side provisioner.
        print(
            "runtime database boundary check failed "
            f"({type(exc).__name__})",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
