from __future__ import annotations

import argparse
import asyncio
import os

from .collection import run_once, worker_identity
from .database import Base, SessionLocal, engine
from .investigations import bootstrap_legacy_investigations, run_review_task_once


async def _worker_loop(*, poll_seconds: float, slot: int) -> None:
    worker_id = f"{worker_identity()}:slot-{slot}"
    prefer_review = True
    while True:
        if prefer_review:
            completed = await run_review_task_once(worker_id=f"{worker_id}:review")
            if completed is None:
                completed = await run_once(worker_id=worker_id)
        else:
            completed = await run_once(worker_id=worker_id)
            if completed is None:
                completed = await run_review_task_once(worker_id=f"{worker_id}:review")
        prefer_review = not prefer_review
        if completed is None:
            await asyncio.sleep(poll_seconds)


async def _run_loop(*, poll_seconds: float, concurrency: int) -> None:
    # Keep lease sizing aligned with the actual worker count selected by the CLI.
    # This must happen in-process because command-line concurrency can differ from
    # the inherited environment (for example, after Compose variable expansion).
    os.environ["PLDR_COLLECTOR_CONCURRENCY"] = str(concurrency)
    await asyncio.gather(*(
        _worker_loop(poll_seconds=poll_seconds, slot=slot)
        for slot in range(1, concurrency + 1)
    ))


async def _run_single() -> None:
    worker_id = worker_identity()
    completed = await run_review_task_once(worker_id=f"{worker_id}:review")
    if completed is None:
        await run_once(worker_id=worker_id)


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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="number of bounded worker slots for --loop (default: 4)",
    )
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    if args.concurrency <= 0 or args.concurrency > 32:
        parser.error("--concurrency must be between 1 and 32")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        bootstrap_legacy_investigations(session)
    try:
        if args.once:
            asyncio.run(_run_single())
        else:
            asyncio.run(_run_loop(poll_seconds=args.poll_seconds, concurrency=args.concurrency))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
