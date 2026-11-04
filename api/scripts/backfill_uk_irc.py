"""Backfill UK/Irish IRC results: Warsash, Hamble Winter Series, HalSail clubs, Sailwave seeds.

Runs each seed separately (so a long run keeps what it already stored and
one broken seed does not stop the rest) through the same ``sailwave.run`` /
``halsail.run`` the scheduled runners use, with no ``since`` (all history).
Prints one JSON line per seed.

    PYTHONPATH=src python scripts/backfill_uk_irc.py [warsash hamble halsail sailwave] [--from SUBSTR]

``--from`` resumes at the first seed whose URL contains SUBSTR; ``--only A,B``
runs just the seeds whose URL contains one of the comma-separated strings.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime


def _emit(**kw) -> None:
    kw["at"] = datetime.now().isoformat(timespec="seconds")
    print(json.dumps(kw, default=str), flush=True)


def main(groups: list[str], start_at: str | None = None, only: list[str] | None = None) -> None:
    from irc_data.scrapers import halsail, sailwave
    from irc_data.scrapers.halsail_seeds import seeds_for as hal_seeds
    from irc_data.scrapers.sailwave_seeds import seeds_for as sw_seeds
    from irc_data.temporal.legacy_adapters import _assert_approved

    plan = {
        "warsash": [("sailwave", "warsash-spring-series", s) for s in sw_seeds("warsash-spring-series")],
        "hamble": [("halsail", "hamble-winter-series", s) for s in hal_seeds("hamble-winter-series")],
        "halsail": [("halsail", "halsail", s) for s in hal_seeds("halsail")],
        "sailwave": [("sailwave", "sailwave", s) for s in sw_seeds("sailwave")],
    }
    skipping = bool(start_at)
    for group in groups:
        for kind, source, seed in plan[group]:
            if skipping and start_at not in seed.url:
                continue
            if only and not any(o in seed.url for o in only):
                continue
            skipping = False
            started = time.monotonic()
            try:
                _assert_approved(source, kind)
                mod = sailwave if kind == "sailwave" else halsail
                stats = mod.run(source=source, seeds=[seed], since=None)
                _emit(group=group, source=source, seed=seed.url, club=seed.club, ok=True,
                      seconds=round(time.monotonic() - started), stats=stats)
            except Exception as e:  # noqa: BLE001 - record and continue with the next seed
                _emit(group=group, source=source, seed=seed.url, club=seed.club, ok=False,
                      seconds=round(time.monotonic() - started),
                      error=f"{type(e).__name__}: {e}"[:2000],
                      trace=traceback.format_exc()[-1500:])


if __name__ == "__main__":
    args = sys.argv[1:]
    start = None
    if "--from" in args:
        i = args.index("--from")
        start = args[i + 1]
        args = args[:i] + args[i + 2:]
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = args[i + 1].split(",")
        args = args[:i] + args[i + 2:]
    main(args or ["warsash", "hamble", "halsail", "sailwave"], start, only)
