"""OPS-02-05 — Migrate crontab to Temporal schedules, one source at a time.

**Goal:** no production ingestion left on cron.

This module is the *plan + ledger* behind the migration.  It is deliberately
pure-Python and deterministic so the whole migration can be reviewed as a
ledger (the issue's Verification step is "Ledger review") without needing a
live Temporal server, database, or the production crontab.

Scope (from the issue)
----------------------
Per source: **enable** the Temporal schedule, keep the cron line until
**two green Temporal runs** (``source_runs``), then **delete** the cron line
and commit ``crontab.txt``.

Migration order (scope): ORC, TCC, SailSys, TopYacht, ISORA, RHKYC,
SailRaceHQ, cert discovery/parse, wayback.  Watchdog, health-check,
refresh-views and log cleanup move **last**.

Acceptance criteria
-------------------
1. ``crontab.txt`` contains no ``irc-data scrape`` lines.
2. Every source's last run is in ``source_runs`` (the Temporal run ledger).
3. Admin Scrapers page green for 7 days.

Deliverable / output contract: ``crontab.txt`` diff + 7-day ledger
(:class:`OPS0205ReportV1`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

#: Schema version of the ledger report contract.
CRON_MIGRATION_SCHEMA_VERSION = "ops-02-05-v1"

#: Number of consecutive green Temporal (``source_runs``) runs a source must
#: have before its cron line may be deleted (issue scope: "two green
#: Temporal runs").
REQUIRED_GREEN_RUNS = 2

#: Number of consecutive days the admin Scrapers page must be green before
#: the migration is considered accepted (acceptance criterion 3).
REQUIRED_GREEN_DAYS = 7


# ---------------------------------------------------------------------------
# Cron-line classification
# ---------------------------------------------------------------------------

#: Matches an *active* (non-comment) cron line whose command invokes the
#: ``irc-data`` CLI.  The command may be prefixed with ``cd`` / ``bash -c`` /
#: an ``op run`` wrapper, so we look for the ``irc-data <subcommand>`` token
#: anywhere in the command part of a non-comment line.
_ACTIVE_LINE_RE = re.compile(r"^\s*(?!\s*#)(?:\S+\s+){5}(?P<cmd>.*)$", re.M)
_IRC_DATA_RE = re.compile(r"\birc-data\s+(?P<sub>[a-z][a-z0-9-]*)")


@dataclass(frozen=True)
class CronLine:
    """One classified cron line.

    Attributes
    ----------
    lineno:
        1-based line number in the source file.
    raw:
        The raw line text (without trailing newline).
    command:
        The command portion (empty for comments / env lines / unparseable).
    subcommand:
        The ``irc-data`` sub-command invoked (``scrape``, ``health-check``,
        …) or ``None`` if this line does not invoke ``irc-data``.
    scrape_target:
        For ``irc-data scrape …`` lines, the first positional argument
        (``orc``, ``tcc``, ``pdf-certs``, ``results``, ``raw-capture``,
        ``wayback``, ``certs`` …) or ``""``.
    source_flag:
        The value of a ``--source <value>`` option on the line (e.g.
        ``sailsys`` in ``scrape results --source sailsys``), or ``None``.
    is_scrape:
        True when this is an active ``irc-data scrape …`` production
        ingestion line — the exact class of line the acceptance criterion
        forbids in the final crontab.
    is_irc_data:
        True when this is any active ``irc-data …`` invocation (includes
        scrape *and* the late-moving ops commands: watchdog, health-check,
        refresh-views, rematch, …).
    """

    lineno: int
    raw: str
    command: str
    subcommand: str | None
    scrape_target: str | None
    source_flag: str | None
    is_scrape: bool
    is_irc_data: bool
    #: every ``irc-data`` sub-command on the line (a compound ``a && b``
    #: cron line invokes more than one); empty when not an irc-data line.
    all_subcommands: tuple[str, ...] = ()

    @property
    def removable(self) -> bool:
        """A line is removable once its source has completed the migration.

        Comments, env assignments and already-removed lines are never
        "removable" (they are either kept verbatim or not scrape lines).
        """
        return self.is_irc_data


def _classify_line(lineno: int, raw: str) -> CronLine:
    stripped = raw.strip()
    subcommand: str | None = None
    scrape_target: str | None = None
    source_flag: str | None = None
    all_subcommands: tuple[str, ...] = ()
    command = ""

    # Only consider active cron entries (5 time fields + command).
    m = re.match(r"^\s*(?:\S+\s+){5}(?P<cmd>.*)$", raw)
    is_comment = stripped.startswith("#") or not stripped
    if m and not is_comment and "=" not in raw.split(None, 1)[0]:
        command = m.group("cmd")
        im = _IRC_DATA_RE.search(command)
        if im:
            subcommand = im.group("sub")
            all_subcommands = tuple(x.group("sub") for x in _IRC_DATA_RE.finditer(command))
            # capture `--source <value>` anywhere in the command
            sm = re.search(r"--source[=\s]+([A-Za-z0-9_-]+)", command)
            if sm:
                source_flag = sm.group(1)
            if subcommand == "scrape":
                # first positional token after `irc-data scrape`; strip the
                # surrounding shell quotes left by the `bash -c "…"` wrapper.
                rest = command[im.end():].strip()
                target = ""
                for tok in rest.split():
                    if tok.startswith("--"):
                        continue
                    target = tok
                    break
                scrape_target = target.strip("\"'")

    is_scrape = subcommand == "scrape"
    return CronLine(
        lineno=lineno,
        raw=raw,
        command=command,
        subcommand=subcommand,
        scrape_target=scrape_target,
        source_flag=source_flag,
        is_scrape=is_scrape,
        is_irc_data=subcommand is not None,
        all_subcommands=all_subcommands,
    )


def parse_crontab(text: str) -> list[CronLine]:
    """Classify every line of *text* (a ``crontab.txt``)."""
    return [_classify_line(i, line) for i, line in enumerate(text.splitlines(), start=1)]


def scrape_lines(lines: Iterable[CronLine]) -> list[CronLine]:
    """Return the active ``irc-data scrape`` lines (production ingestion)."""
    return [ln for ln in lines if ln.is_scrape]


def irc_data_lines(lines: Iterable[CronLine]) -> list[CronLine]:
    """Return all active ``irc-data …`` lines (scrape + ops commands)."""
    return [ln for ln in lines if ln.is_irc_data]


def remove_lines(text: str, lines: Iterable[CronLine]) -> str:
    """Return *text* with the given lines removed (drop, keep order).

    Comment headers and env assignments are preserved verbatim.  Only the
    classified lines are dropped.
    """
    drop = {ln.lineno for ln in lines}
    kept = [l for i, l in enumerate(text.splitlines(), start=1) if i not in drop]
    # collapse 3+ consecutive blank lines left behind down to one
    out: list[str] = []
    blank = 0
    for line in kept:
        if line.strip() == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(line)
    # tidy trailing whitespace/newline
    result = "\n".join(out).rstrip("\n") + "\n"
    return result


def render_diff(before: str, after: str, path: str = "api/crontab.txt") -> str:
    """Render a unified diff of the crontab migration (the deliverable)."""
    import difflib

    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(diff)


# ---------------------------------------------------------------------------
# Migration plan & per-source ledger
# ---------------------------------------------------------------------------

#: The migration order from the issue scope.  Sources first (ORC … wayback),
#: then the late-moving ops jobs (watchdog, health-check, refresh-views,
#: log cleanup) last.
#:
#: ``register_slug`` is the ``data_sources.slug`` the OPS-01-02
#: ``SourceRunWorkflow`` schedule is keyed on (``source-<slug>``).
#: ``cron_markers`` are the ``irc-data`` invocation signatures this step's
#: cron line(s) match (subcommand + optional scrape target / flag).
@dataclass(frozen=True)
class MigrationStep:
    key: str                      # stable step key (ledger identity)
    label: str                    # human label
    register_slug: str | None     # data_sources.slug (None for non-register ops jobs)
    cadence: str                  # register cadence driving the Temporal schedule
    order: int                    # position in the migration sequence
    late: bool                    # True → moves last (watchdog/health/refresh/cleanup)
    cron_subcommand: str          # irc-data subcommand on the cron line
    cron_scrape_target: str | None = None   # scrape target when subcommand == scrape
    cron_source_flag: str | None = None     # `--source <value>` on the cron line


def _build_plan() -> list[MigrationStep]:
    """Build the canonical OPS-02-05 migration plan (scope order).

    The scope names the *headline* per-source order; the production crontab
    also carries additional ingestion lines (raw-capture feeds, the results
    rematcher, identity/design maintenance).  Every production ingestion /
    ops line must migrate before the acceptance criterion "no ``irc-data``
    scrape lines" is met, so they are folded into the plan:

    * Sources migrate in the scope order, with the additional ``results``
      sources slotted next to their headline siblings and the raw-capture /
      maintenance lines after the named sources.
    * Watchdog, health-check, refresh-views and log cleanup always move last.
    """
    steps: list[MigrationStep] = []

    def add(key, label, slug, cadence, late, sub, target=None, source_flag=None):
        steps.append(MigrationStep(
            key=key, label=label, register_slug=slug, cadence=cadence,
            order=len(steps), late=late, cron_subcommand=sub,
            cron_scrape_target=target, cron_source_flag=source_flag,
        ))

    # --- Sources, in the scope's order -------------------------------------
    add("orc", "ORC (daily snapshot)", "orc", "daily", False, "scrape", "orc")
    add("tcc", "IRC TCC listing", "irc-tcc", "daily", False, "scrape", "tcc")
    # IRC certificate PDF raw capture rides with the TCC / cert pipeline.
    add("pdf-certs", "IRC certificate PDF raw capture", "irc-certs", "nightly", False, "scrape", "pdf-certs")
    add("sailsys", "SailSys race results", "sailsys", "30min", False, "scrape", "results", source_flag="sailsys")
    add("topyacht", "TopYacht race results", "topyacht", "nightly", False, "scrape", "results", source_flag="topyacht")
    add("isora", "ISORA race results", "isora", "weekly", False, "scrape", "results", source_flag="isora")
    add("rhkyc", "RHKYC race results", "rhkyc", "weekly", False, "scrape", "results", source_flag="rhkyc")
    add("sailracehq", "SailRaceHQ race results", "sailracehq", "nightly", False, "scrape", "results", source_flag="sailracehq")
    add("cert-discovery", "IRC cert discovery (exhaustive)", "irc-certs", "weekly", False, "scrape", "certs")
    add("cert-parse", "IRC cert PDF parse", "irc-certs", "weekly", False, "parse-certs")
    add("wayback", "Wayback Machine backfill", "wayback-irc", "monthly", False, "scrape", "wayback")

    # --- Additional production ingestion lines (raw captures / feeds) ------
    add("raw-sailwave", "Raw capture — Sailwave result files", "sailwave", "nightly", False, "scrape", "raw-capture", source_flag="sailwave")
    add("raw-sailing-news", "Raw capture — sailing news feeds", "sailing-news", "hourly", False, "scrape", "raw-capture", source_flag="sailing-news")
    add("raw-dp-00-03", "Raw capture — YachtScoring + Manage2Sail", "yachtscoring", "nightly", False, "scrape", "raw-capture", source_flag="dp-00-03")

    # --- Maintenance (identity / design / rematch) --------------------------
    add("match-boats", "Match ORC certs to IRC boats", None, "weekly", False, "match-boats")
    add("seed-designs", "Re-seed designs + backfill", None, "monthly", False, "seed-designs")
    add("seed-design-designers", "Seed design designers + backfill boat identity", None, "daily", False, "seed-design-designers")
    add("rematch-results", "Re-match unmatched results", None, "30min", False, "rematch-results")

    # --- Late movers (watchdog, health-check, refresh-views, log cleanup) --
    add("watchdog", "Staleness watchdog", None, "quarterhourly", True, "scrape-watchdog")
    add("health-check", "Daily health check (--notify)", None, "daily", True, "health-check")
    add("scraper-health", "Scraper health probe", None, "daily", True, "scraper-health")
    add("refresh-views", "Refresh materialized views", None, "daily", True, "refresh-views")
    add("log-cleanup", "Log cleanup (find -mtime +30 -delete)", None, "daily", True, "find")
    return steps


#: The canonical plan, in order.
MIGRATION_PLAN: list[MigrationStep] = _build_plan()

#: Keys in migration order (sources first, late movers last).
MIGRATION_ORDER: list[str] = [s.key for s in MIGRATION_PLAN]


def step_for(key: str) -> MigrationStep:
    for s in MIGRATION_PLAN:
        if s.key == key:
            return s
    raise KeyError(f"unknown migration step: {key!r}")


def _line_matches_step(line: CronLine, step: MigrationStep) -> bool:
    """Does an active cron line belong to *step*?

    Matches on the ``irc-data`` subcommand and, for ``scrape`` lines, the
    scrape target *and* the ``--source`` flag (so the several
    ``scrape results --source <slug>`` / ``scrape raw-capture --source
    <feed>`` lines each map to exactly one step).  ``parse-certs`` is its
    own subcommand.  ``log-cleanup`` matches the ``find … -delete`` line.
    """
    if step.key == "log-cleanup":
        return line.command.startswith("find ") and "-delete" in line.command
    if not line.is_irc_data:
        return False
    # A compound line (``irc-data a … && irc-data b …``) belongs to a step
    # when the step's subcommand appears anywhere on the line.
    subs = line.all_subcommands or ((line.subcommand,) if line.subcommand else ())
    if step.cron_subcommand not in subs:
        return False
    if step.cron_subcommand == "scrape":
        if line.scrape_target != step.cron_scrape_target:
            return False
        # disambiguate lines that share a scrape target via --source
        return line.source_flag == step.cron_source_flag
    # non-scrape irc-data subcommands match on subcommand presence
    return True


#: Per-step migration states.
STATE_PENDING = "pending"            # not yet started
STATE_SCHEDULED = "schedule_enabled" # Temporal schedule enabled; cron line still present
STATE_GREEN = "temporal_green"       # >= REQUIRED_GREEN_RUNS green source_runs
STATE_DONE = "cron_removed"          # cron line deleted & committed


@dataclass
class StepLedger:
    """Ledger for one migration step (one source / ops job)."""

    step: MigrationStep
    state: str = STATE_PENDING
    green_runs: int = 0
    cron_line_present: bool = True
    history: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: str, **detail: Any) -> None:
        self.history.append({"event": event, **detail})

    # -- transitions ---------------------------------------------------------
    def enable_schedule(self) -> None:
        if self.state != STATE_PENDING:
            raise ValueError(f"{self.step.key}: cannot enable from state {self.state}")
        self.state = STATE_SCHEDULED
        self.record("schedule_enabled", schedule_id=(
            f"source-{self.step.register_slug}" if self.step.register_slug else None))

    def record_temporal_run(self, success: bool) -> None:
        """Record a Temporal (``source_runs``) run outcome for this step."""
        if self.state == STATE_PENDING:
            raise ValueError(f"{self.step.key}: schedule not enabled yet")
        if success:
            self.green_runs += 1
        else:
            self.green_runs = 0  # consecutive green required
        self.record("temporal_run", success=success, consecutive_green=self.green_runs)
        if self.green_runs >= REQUIRED_GREEN_RUNS and self.state == STATE_SCHEDULED:
            self.state = STATE_GREEN
            self.record("green_threshold_met", required=REQUIRED_GREEN_RUNS)

    def remove_cron_line(self) -> None:
        if self.state != STATE_GREEN:
            raise ValueError(
                f"{self.step.key}: cron line may only be removed after "
                f"{REQUIRED_GREEN_RUNS} green Temporal runs (state={self.state}, "
                f"green={self.green_runs})"
            )
        self.cron_line_present = False
        self.state = STATE_DONE
        self.record("cron_line_removed")

    @property
    def ready_to_remove(self) -> bool:
        return self.state == STATE_GREEN


class MigrationLedger:
    """The per-source migration ledger (the reviewable artefact).

    Enforces the scope invariant: a cron line is removed only after the
    source's Temporal schedule produced ``REQUIRED_GREEN_RUNS`` consecutive
    green runs recorded in ``source_runs``.
    """

    def __init__(self, plan: Sequence[MigrationStep] | None = None):
        self.plan = list(plan or MIGRATION_PLAN)
        self.steps: dict[str, StepLedger] = {s.key: StepLedger(step=s) for s in self.plan}
        # wire the order enforced as: sources in `order`, then late movers
        self._order = [s.key for s in sorted(self.plan, key=lambda s: (s.late, s.order))]

    @property
    def order(self) -> list[str]:
        return list(self._order)

    def step(self, key: str) -> StepLedger:
        return self.steps[key]

    # convenience pass-throughs ---------------------------------------------
    def enable_schedule(self, key: str) -> None:
        self.steps[key].enable_schedule()

    def record_temporal_run(self, key: str, success: bool) -> None:
        self.steps[key].record_temporal_run(success)

    def remove_cron_line(self, key: str) -> None:
        self.steps[key].remove_cron_line()

    # roll-ups ----------------------------------------------------------------
    @property
    def completed(self) -> list[str]:
        return [k for k in self._order if self.steps[k].state == STATE_DONE]

    @property
    def remaining_cron(self) -> list[str]:
        return [k for k in self._order if self.steps[k].cron_line_present]

    @property
    def sources_done(self) -> bool:
        """All *source* steps (non-late) have had their cron line removed."""
        return all(
            self.steps[s.key].state == STATE_DONE
            for s in self.plan if not s.late
        )

    def apply_to_crontab(self, text: str) -> str:
        """Remove from *text* the cron lines of every step in state DONE."""
        lines = parse_crontab(text)
        to_drop: list[CronLine] = []
        for key in self._order:
            led = self.steps[key]
            if led.state == STATE_DONE:
                to_drop.extend(ln for ln in lines if _line_matches_step(ln, led.step))
        return remove_lines(text, to_drop)


# ---------------------------------------------------------------------------
# 7-day-green admin Scrapers page roll-up (acceptance criterion 3)
# ---------------------------------------------------------------------------


def scrapers_page_green_days(
    daily_status: Iterable[tuple[str, bool]],
) -> int:
    """Count the trailing run of consecutive green days.

    *daily_status* is an iterable of ``(iso_date, green)`` ordered oldest →
    newest.  Returns the length of the trailing streak of green days.
    """
    streak = 0
    for _date, green in daily_status:
        streak = streak + 1 if green else 0
    return streak


# ---------------------------------------------------------------------------
# OPS-02-05 report contract (Deliverable: crontab.txt diff + 7-day ledger)
# ---------------------------------------------------------------------------


@dataclass
class OPS0205ReportV1:
    """Signed-review artefact for ledger review (the Verification step)."""

    schema_version: str
    generated_at: str
    diff: str
    acceptance: dict[str, bool]
    steps: list[dict[str, Any]]
    green_days: int
    overall_pass: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "green_days": self.green_days,
            "acceptance": dict(self.acceptance),
            "overall_pass": self.overall_pass,
            "steps": [dict(s) for s in self.steps],
            "diff": self.diff,
        }


def build_report(
    before_crontab: str,
    ledger: MigrationLedger,
    *,
    scrapers_green_streak: int,
    generated_at: datetime | None = None,
    crontab_path: str = "api/crontab.txt",
) -> OPS0205ReportV1:
    """Assemble the OPS-02-05 review report.

    * ``crontab_no_scrape_lines`` — after applying the ledger, the crontab
      contains no active ``irc-data scrape`` line.
    * ``every_source_last_run_in_source_runs`` — every *source* step reached
      the ``temporal_green``/``cron_removed`` state, which by construction
      requires ``source_runs`` rows (the run ledger).
    * ``scrapers_page_green_7_days`` — trailing green streak ≥ 7.
    """
    after = ledger.apply_to_crontab(before_crontab)
    after_lines = parse_crontab(after)
    remaining_scrape = scrape_lines(after_lines)

    ac_no_scrape = len(remaining_scrape) == 0
    ac_ledger = ledger.sources_done
    ac_green = scrapers_green_streak >= REQUIRED_GREEN_DAYS

    steps_payload = []
    for key in ledger.order:
        led = ledger.steps[key]
        steps_payload.append({
            "key": key,
            "label": led.step.label,
            "register_slug": led.step.register_slug,
            "schedule_id": (
                f"source-{led.step.register_slug}" if led.step.register_slug else None
            ),
            "late": led.step.late,
            "state": led.state,
            "consecutive_green_runs": led.green_runs,
            "cron_line_present": led.cron_line_present,
            "required_green_runs": REQUIRED_GREEN_RUNS,
            "history": list(led.history),
        })

    acceptance = {
        "crontab_no_scrape_lines": ac_no_scrape,
        "every_source_last_run_in_source_runs": ac_ledger,
        "scrapers_page_green_7_days": ac_green,
    }
    overall = all(acceptance.values())

    ts = (generated_at or datetime.now(timezone.utc)).isoformat()
    return OPS0205ReportV1(
        schema_version=CRON_MIGRATION_SCHEMA_VERSION,
        generated_at=ts,
        diff=render_diff(before_crontab, after, path=crontab_path),
        acceptance=acceptance,
        steps=steps_payload,
        green_days=scrapers_green_streak,
        overall_pass=overall,
    )


__all__ = [
    "CRON_MIGRATION_SCHEMA_VERSION",
    "REQUIRED_GREEN_RUNS",
    "REQUIRED_GREEN_DAYS",
    "CronLine",
    "MigrationStep",
    "StepLedger",
    "MigrationLedger",
    "MIGRATION_PLAN",
    "MIGRATION_ORDER",
    "STATE_PENDING",
    "STATE_SCHEDULED",
    "STATE_GREEN",
    "STATE_DONE",
    "OPS0205ReportV1",
    "step_for",
    "parse_crontab",
    "scrape_lines",
    "irc_data_lines",
    "remove_lines",
    "render_diff",
    "scrapers_page_green_days",
    "build_report",
]
