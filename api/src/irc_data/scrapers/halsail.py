"""HalSail club results (Hamble Winter Series, Island SC, ...): IRC only.

Collection conditions (Stuart's approval, 2026-11-03) and how they are enforced:

* Only public ``/Result/Club|Class|Event/{id}`` pages that clubs link from
  their own sites. Seeds are validated against :data:`SEED_URL_RE`; those
  pages redirect (in-page script) to ``halsail-1e484.kxcdn.com/Result/Public/{id}``,
  which is the page we read. Other classes/series of the same club are reached
  only through that page's own class/series selectors, not by building URLs.
* At least 2 s between requests (navigations and selector changes) with the
  identifying policy User-Agent.
* Never fetch ``/Result/PrintRace`` or ``/Result/PrintOverall`` (robots), never
  call ``/HalApi`` (no issued key) and never call the JSON endpoints behind the
  pages ourselves: the browser context aborts any request to those paths
  (:data:`FORBIDDEN_PATH_RE`), and robots.txt is checked before each seed.
  The results table is JS-rendered, so pages are rendered in headless Chromium.
* ``source_url`` is stored on every row (``https://halsail.com/Result/Public/{series}``).

Rows: one per boat per race, from the per-race tables (place, Hcap = TCC,
elapsed, corrected). Classes are kept only when their name carries an IRC
token (seed ``class_include`` can override) and every row needs a plausible
TCC. DNC rows are dropped.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from bs4 import BeautifulSoup, Tag

from irc_data.scrapers.result_base import NormalizedResult
from irc_data.scrapers.sailwave import (
    _NON_FINISH,
    _OTHER_SYSTEMS_RE,
    _clean,
    find_status,
    parse_duration,
    parse_tcc,
)

logger = logging.getLogger(__name__)

SOURCE = "halsail"

SEED_URL_RE = re.compile(
    r"^https://(www\.|archive\.)?halsail\.com/Result/(Club|Class|Event)/\d+(\?[A-Za-z&=]*)?$",
    re.IGNORECASE,
)
#: Never requested, not even by the page's own scripts (robots / no API key).
#: The rendering page's own partial requests (e.g. /Result/_Boat/{id}) are
#: the browser rendering the public page; we never call them directly.
FORBIDDEN_PATH_RE = re.compile(r"/Result/Print(Race|Overall)|/HalApi", re.IGNORECASE)
MIN_DELAY = 2.5

_IRC_CLASS_RE = re.compile(r"(?<![A-Z])IRC", re.IGNORECASE)
_RACE_CAPTION_RE = re.compile(r"^Race\s+(\d+)\b", re.IGNORECASE)
_CAPTION_DATE_RE = re.compile(r"(\d{1,2}\s+[A-Za-z]{3,}\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})")


@dataclass(frozen=True)
class HalSailSeed:
    url: str
    club: str
    source: str = SOURCE
    class_include: str = r"(?<![A-Za-z])IRC"
    class_exclude: str | None = None
    series: str | None = None
    note: str = ""

    def validate(self) -> None:
        if not SEED_URL_RE.match(self.url) or FORBIDDEN_PATH_RE.search(self.url):
            raise ValueError(f"not a public club-linked HalSail result page: {self.url}")

    def wants_class(self, name: str) -> bool:
        if not re.search(self.class_include, name, re.IGNORECASE):
            return False
        if self.class_exclude and re.search(self.class_exclude, name, re.IGNORECASE):
            return False
        stripped = _IRC_CLASS_RE.sub(" ", name)
        return not _OTHER_SYSTEMS_RE.search(stripped)


# ---------------------------------------------------------------------------
# Parsing a rendered Public page (pure; tested with fixtures)
# ---------------------------------------------------------------------------


@dataclass
class SelectorOption:
    value: str
    text: str
    has_results: bool


def parse_selectors(html: str) -> dict[str, Any]:
    """Classes (``#ddRacingClasses``) and each class's series (``#dd{class}``)."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {"club": None, "classes": [], "series": {}}
    h1 = soup.find("h1")
    if h1 is not None:
        a = h1.find("a")
        out["club"] = _clean((a or h1).get_text(" ")).removeprefix("Results from ").strip() or None
    out["layout"] = "public"
    if soup.find("select", id="ddClass") is not None:
        # archive.halsail.com: season (ddArchive) -> class (ddClass) -> series (ddSeries)
        out["layout"] = "archive"
        out["archives"] = _options(soup.find("select", id="ddArchive")) \
            if soup.find("select", id="ddArchive") is not None else []
        out["classes"] = _options(soup.find("select", id="ddClass"))
        ser = soup.find("select", id="ddSeries")
        out["series_current"] = _options(ser) if ser is not None else []
        for o in out["archives"] + out["classes"] + out["series_current"]:
            o.has_results = True
        return out
    sel = soup.find("select", id="ddRacingClasses")
    if sel is not None:
        out["classes"] = _options(sel)
        for opt in out["classes"]:
            ssel = soup.find("select", id=f"dd{opt.value}")
            out["series"][opt.value] = _options(ssel) if ssel is not None else []
    return out


def _options(sel: Tag) -> list[SelectorOption]:
    opts = []
    for o in sel.find_all("option"):
        grp = o.find_parent("optgroup")
        label = (grp.get("label") or "").lower() if grp is not None else ""
        opts.append(SelectorOption(
            value=str(o.get("value") or ""),
            text=_clean(o.get_text(" ")),
            has_results=("no results" not in label),
        ))
    return opts


def _norm_header(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


_COLS = {
    "place": "place", "rank": "place", "sail": "sail", "sailnumber": "sail",
    "type": "design", "hcap": "hcap", "handicap": "hcap", "rating": "hcap",
    "name": "boat", "owner": "owner", "helm": "helm", "club": "club", "finish": "finish",
    "elapsed": "elapsed", "corrected": "corrected", "points": "points", "laps": "laps",
}


def parse_public_results(
    html: str,
    *,
    class_name: str,
    series_name: str,
    series_id: str,
    class_id: str = "",
    club: str | None = None,
    seed_url: str = "",
    event_series: str | None = None,
    since: date | None = None,
    source_url: str | None = None,
) -> list[NormalizedResult]:
    """Per-race rows from the first layout copy of ``#divResults``.

    The page repeats the same tables once per responsive breakpoint; only
    the first ``.sizeframe`` is read.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id="divResults") or soup
    frame = root.find("div", class_="sizeframe") or root
    source_url = source_url or f"https://halsail.com/Result/Public/{series_id}"
    out: list[NormalizedResult] = []
    for table in frame.find_all("table"):
        cap = table.find("caption")
        cap_text = _clean(cap.get_text(" ")) if cap is not None else ""
        m = _RACE_CAPTION_RE.match(cap_text)
        dm = _CAPTION_DATE_RE.search(cap_text)
        if not m or not dm or re.search(r"\bCancelled\b|\bAbandoned\b", cap_text, re.I):
            continue
        race_no = int(m.group(1))
        race_date = _parse_date(dm.group(1))
        if race_date is None or (since is not None and race_date < since):
            continue
        container = table.find_parent(attrs={"data-raceid": True})
        race_id = container.get("data-raceid") if container is not None else None
        heads = [_COLS.get(_norm_header(th.get_text(" "))) for th in table.select("thead th")]
        if "boat" not in heads or "hcap" not in heads:
            continue
        staged = []
        for tr in table.select("tbody tr"):
            cells = [_clean(td.get_text(" ")) for td in tr.find_all("td")]
            row = {h: cells[i] for i, h in enumerate(heads) if h and i < len(cells)}
            status = find_status(row.get("place"), row.get("corrected"), row.get("elapsed"),
                                 row.get("finish"), row.get("points"))
            if status == "DNC":
                continue
            tcc = parse_tcc(row.get("hcap"))
            boat = row.get("boat") or ""
            sail = re.sub(r"\s+", "", row.get("sail") or "")
            if tcc is None or not (boat or sail):
                continue
            pm = re.match(r"^(\d+)", row.get("place") or "")
            finished = status is None or status not in _NON_FINISH
            place = int(pm.group(1)) if (pm and finished) else None
            staged.append((row, status, tcc, boat, sail, place))
        n = len(staged)
        for row, status, tcc, boat, sail, place in staged:
            raw = {
                "boat_name": boat or sail,
                "sail_number": sail or None,
                "design": row.get("design") or None,
                "owner": row.get("owner") or None,
                "helm": row.get("helm") or None,
                "club": row.get("club") or None,
                "points": row.get("points") or None,
                "place_text": row.get("place") or None,
                "race_date": race_date.isoformat(),
                "race_caption": cap_text[:200],
                "halsail_race_id": race_id,
                "halsail_series_id": series_id,
                "halsail_class_id": class_id or None,
                "halsail_series": series_name,
                "seed_url": seed_url or None,
                "rating_system": "IRC",
                "row_origin": "race_table",
            }
            if status:
                raw["status"] = status
            out.append(NormalizedResult(
                boat_name=boat or sail,
                sail_number=sail or None,
                event_name=event_name_for(class_name, series_name, race_date),
                event_date=race_date,
                event_series=event_series,
                organizing_club=club,
                race_name=f"Race {race_no}",
                race_number=race_no,
                place=place,
                fleet_size=n,
                class_name=class_name,
                class_place=place,
                class_fleet_size=n,
                status=status if (status and status in _NON_FINISH) else "finished",
                rating_type="irc_tcc",
                rating_value=tcc,
                elapsed_time=parse_duration(row.get("elapsed")),
                corrected_time=parse_duration(row.get("corrected")),
                source_url=source_url,
                raw_data={k: v for k, v in raw.items() if v is not None},
            ))
    return out


def event_name_for(class_name: str, series_name: str, race_date: date) -> str:
    name = _clean(series_name)
    if not name or re.fullmatch(r"(new\s+)?series", name, re.I):
        name = _clean(class_name)
    if not re.search(r"(?<!\d)20\d\d(?!\d)", name):
        name = f"{name} {race_date.year}"
    return name


def _parse_date(text: str) -> date | None:
    from datetime import datetime

    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(_clean(text), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Browser collection
# ---------------------------------------------------------------------------


class HalSailBrowser:
    """Headless Chromium with the policy UA, pacing and forbidden-path blocking."""

    def __init__(self, min_delay: float = MIN_DELAY):
        from irc_data.scrapers.polite_http import PoliteClient, policy_user_agent

        self.min_delay = max(2.0, min_delay)
        self._last = 0.0
        self.blocked: list[str] = []
        self.navigations = 0
        self._robots = PoliteClient()
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(user_agent=policy_user_agent())
        self._ctx.route("**/*", self._route)
        self.page = self._ctx.new_page()

    def _route(self, route):
        url = route.request.url
        if FORBIDDEN_PATH_RE.search(url) or "google-analytics" in url or "googletagmanager" in url:
            if FORBIDDEN_PATH_RE.search(url):
                self.blocked.append(url)
            return route.abort()
        return route.continue_()

    def pace(self) -> None:
        delay = self.min_delay + random.uniform(0, 1.0)
        wait = delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def open_seed(self, url: str) -> str:
        from irc_data.scrapers.polite_http import RobotsDisallowedError

        if not self._robots.allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}")
        self.pace()
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.navigations += 1
        if "/Result/Public/" not in self.page.url:
            # Club/Class/Event pages forward to the public page by script.
            self.page.wait_for_url("**/Result/Public/**", timeout=20000)
            self.navigations += 1
        self.page.wait_for_load_state("networkidle", timeout=60000)
        self._check_blocked()
        return self.page.content()

    def choose(self, select_id: str, value: str) -> str:
        """Change one of the page's own selectors, as a visitor would."""
        self.pace()
        before = self.page.evaluate(
            "() => { const d = document.getElementById('divResults');"
            " return d ? d.innerHTML.length + ':' + d.innerText.slice(0, 400) : ''; }"
        )
        changed = self.page.evaluate(
            """([sel, val]) => { const el = document.getElementById(sel);
                 if (!el) return false; if (el.value === val) return false; el.value = val;
                 if (window.jQuery) { window.jQuery(el).trigger('change'); }
                 else { el.dispatchEvent(new Event('change', {bubbles: true})); }
                 return true; }""",
            [select_id, value],
        )
        if changed:
            # The results panel is swapped in by script; wait until it differs
            # from what was shown before the change.
            try:
                self.page.wait_for_function(
                    "(prev) => { const d = document.getElementById('divResults');"
                    " if (!d) return false;"
                    " const cur = d.innerHTML.length + ':' + d.innerText.slice(0, 400);"
                    " return cur !== prev && d.style.display !== 'none'; }",
                    arg=before, timeout=20000,
                )
            except Exception:  # noqa: BLE001 - identical content is possible
                logger.info("halsail: results panel unchanged after %s=%s", select_id, value)
        time.sleep(1.0)
        self.page.wait_for_load_state("networkidle", timeout=60000)
        self._check_blocked()
        return self.page.content()

    def _check_blocked(self) -> None:
        from irc_data.scrapers.polite_http import SourceBlockedError

        text = self.page.content()[:20000]
        if re.search(r"g-recaptcha|hcaptcha|cf-challenge|Access denied", text, re.I):
            raise SourceBlockedError(f"bot check on {self.page.url}")

    def close(self) -> None:
        for closer in (self._ctx.close, self._browser.close, self._pw.stop, self._robots.close):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass


def _keep(rows_in, rows_out, seen_races, series_key) -> None:
    for r in rows_in:
        rid = (r.raw_data or {}).get("halsail_race_id") or f"{series_key}:{r.race_number}"
        key = f"{rid}|{r.raw_data.get('boat_name')}|{r.sail_number}"
        if key in seen_races:
            continue  # the same race scored in two series of one class
        seen_races.add(key)
        rows_out.append(r)


def _season_years(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", text)]


def scrape_seed(browser: HalSailBrowser, seed: HalSailSeed, since: date | None = None,
                seen_races: set[str] | None = None) -> dict:
    """Render a seed and every IRC class/series reachable from its selectors."""
    seed.validate()
    html = browser.open_seed(seed.url)
    sel = parse_selectors(html)
    club = seed.club or sel["club"]
    rows: list[NormalizedResult] = []
    seen_races = set() if seen_races is None else seen_races
    series_done = 0
    irc_classes: list[str] = []
    classes_total = len(sel["classes"])

    if sel.get("layout") == "archive":
        archives = sel.get("archives") or [None]
        for arch in archives:
            if arch is not None:
                years = _season_years(arch.text)
                if since is not None and years and max(years) < since.year:
                    continue
                html = browser.choose("ddArchive", arch.value)
                sel = parse_selectors(html)
                classes_total = max(classes_total, len(sel["classes"]))
            for cls in [c for c in sel["classes"] if seed.wants_class(c.text)]:
                html = browser.choose("ddClass", cls.value)
                irc_classes.append(cls.text)
                series = parse_selectors(html).get("series_current") or []
                for i, ser in enumerate(series):
                    if i > 0 or (len(series) > 1):
                        html = browser.choose("ddSeries", ser.value)
                    page_url = browser.page.url
                    got = parse_public_results(
                        html, class_name=cls.text, series_name=ser.text,
                        series_id=f"{arch.value if arch else ''}/{ser.value}",
                        class_id=cls.value, club=club, seed_url=seed.url,
                        event_series=seed.series, since=since,
                        source_url=page_url if "/Result/Public/" in page_url else None,
                    )
                    series_done += 1
                    _keep(got, rows, seen_races, f"{arch.value if arch else ''}/{ser.value}")
    else:
        classes = [c for c in sel["classes"] if c.has_results and seed.wants_class(c.text)]
        irc_classes = [c.text for c in classes]
        for cls in classes:
            html = browser.choose("ddRacingClasses", cls.value)
            series = [s for s in sel["series"].get(cls.value, []) if s.has_results]
            for ser in series:
                html = browser.choose(f"dd{cls.value}", ser.value)
                got = parse_public_results(
                    html, class_name=cls.text, series_name=ser.text, series_id=ser.value,
                    class_id=cls.value, club=club, seed_url=seed.url,
                    event_series=seed.series, since=since,
                )
                series_done += 1
                _keep(got, rows, seen_races, ser.value)
    return {
        "rows": rows,
        "club": club,
        "classes_total": classes_total,
        "irc_classes": irc_classes,
        "series": series_done,
        "landing_url": browser.page.url,
    }


def run(record: dict | None = None, *, source: str, seeds, since: date | None = None,
        engine=None, store: bool = True, browser: HalSailBrowser | None = None) -> dict[str, Any]:
    """Render, parse and persist HalSail IRC results for ``seeds``.

    Raises when discovery is broken: every seed failing, or every seed
    rendering without any class selector (page layout changed / blocked).
    """
    from irc_data.db.operations import log_ingestion_end, log_ingestion_start
    from irc_data.scrapers.polite_http import SourceBlockedError
    from irc_data.scrapers.result_import import store_normalized_results

    seeds = list(seeds)
    for s in seeds:
        s.validate()
    own = browser is None
    browser = browser or HalSailBrowser()
    rows: list[NormalizedResult] = []
    per_seed: dict[str, Any] = {}
    errors: list[str] = []
    no_classes = 0
    seen_races: set[str] = set()  # club pages linked twice show the same classes
    try:
        for seed in seeds:
            try:
                got = scrape_seed(browser, seed, since=since, seen_races=seen_races)
            except SourceBlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                errors.append(f"{seed.url}: {type(e).__name__}: {e}"[:300])
                continue
            if got["classes_total"] == 0:
                no_classes += 1
                errors.append(f"{seed.url}: page rendered without a class selector")
            per_seed[seed.url] = {"rows": len(got["rows"]), "irc_classes": got["irc_classes"],
                                  "series": got["series"], "landing": got["landing_url"]}
            rows.extend(got["rows"])
    finally:
        if own:
            browser.close()
    if seeds and (len(errors) >= len(seeds) or no_classes == len(seeds)):
        raise RuntimeError(f"{source}: HalSail discovery failed for every seed: {errors[:3]}")

    stats: dict[str, Any] = {
        "records_written": 0, "records_found": len(rows), "seeds": per_seed,
        "seed_errors": errors, "since": since.isoformat() if since else None,
        "source": source, "navigations": browser.navigations,
        "forbidden_requests_blocked": len(browser.blocked),
    }
    if store and rows:
        if engine is None:
            from irc_data.db.connection import get_engine

            engine = get_engine()
        log_id = log_ingestion_start(engine, source, metadata={
            "adapter": f"legacy-cli:{source}", "since": stats["since"]})
        try:
            stored = store_normalized_results(engine, rows, source=source, log=False, detail=True)
        except Exception as e:
            log_ingestion_end(engine, log_id, status="failed", records_found=len(rows),
                              records_new=0, error_message=str(e)[:1000])
            raise
        errs = errors + stored["error_details"]
        log_ingestion_end(engine, log_id,
                          status="completed" if not errs else "completed_with_errors",
                          records_found=len(rows), records_new=stored["written"],
                          error_message="; ".join(errs)[:1000] if errs else None)
        stats.update(records_written=stored["written"], upserted=stored["imported"],
                     boats_matched_rows=stored["matched"], store_errors=stored["errors"])
    return stats


class HalSailSource:
    """Register ``adapter_class`` target; the legacy runner does the work."""

    def __init__(self, seeds=None, source: str = SOURCE):
        from irc_data.scrapers.halsail_seeds import seeds_for

        self.seeds = list(seeds) if seeds is not None else seeds_for(source)
        self.source = source

    def source_name(self) -> str:
        return self.source
