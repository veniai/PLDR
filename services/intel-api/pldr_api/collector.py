from __future__ import annotations

import argparse
import asyncio

from .collection import run_once, worker_identity
from .database import Base, engine


async def _run_loop(*, poll_seconds: float) -> None:
    worker_id = worker_identity()
    while True:
        completed = await run_once(worker_id=worker_id)
        if completed is None:
            await asyncio.sleep(poll_seconds)


async def _run_single() -> None:
    await run_once(worker_id=worker_identity())


def main() -> int:
    parser = argparse.ArgumentParser(description="PLDR reliable-collection worker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="claim at most one run and exit")
    mode.add_argument("--loop", action="store_true", help="keep claiming durable collection runs")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="idle delay for --loop (default: 2 seconds)",
    )
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    Base.metadata.create_all(bind=engine)
    try:
        if args.once:
            asyncio.run(_run_single())
        else:
            asyncio.run(_run_loop(poll_seconds=args.poll_seconds))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
