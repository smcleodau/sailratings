"""Sailwave HTML results: IRC-only, one row per boat per race.

Sailwave is the scoring program most UK and Irish clubs use. Its published
HTML has a stable structure:

* ``<h1>`` event name, ``<h2>`` venue/club, and a ``<h3 class="seriestitle">``
  such as "Results are provisional as of 20:43 on April 21, 2026" (the
  only place the year of the race dates usually appears).
* Series summaries: ``<h3 class="summarytitle">IRC 1</h3>``, a caption like
  "Sailed: 12, Discards: 1, To count: 11, Rating system: IRC, Entries: 16",
  and ``<table class="summarytable">`` with one column per race
  ("R1<br/>April 18") holding that race's points ("(13.0 DNS)").
* Race tables: ``<h3 class="racetitle">R1 - IRC 1 - April 18</h3>`` and
  ``<table class="racetable">`` with Rank, SailNo, Boat, Class (the boat's
  design), Helm, Rating, Start, Finish, Elapsed, Corrected, Points. Times may
  use dots ("0.48.00") or colons.

Only IRC is kept. A fleet counts as IRC when its summary caption says
"Rating system: IRC", its rating column is headed IRC/TCC, or its fleet name
carries an IRC token without another handicap system's (ECHO, PY, NHC, ...).
UK series often score ECHO, PY and one-design fleets from the same start, so
a rating in the 0.8-1.2 range alone is never taken as IRC. Every stored row
must carry a plausible TCC (0.5-2.0); Sailwave's 2.000 placeholder for boats
without a certificate is dropped, as are DNC rows.

Race tables give place, elapsed and corrected time. A race that appears only
in a summary becomes per-race rows from its points column (place from the
points, no times), flagged ``row_origin='summary'``; the runner drops those
when a race table for the same race exists.

Discovery (``SailwaveSeed``) covers three shapes, all public pages:

* a sailwave.com/results/<club>/ folder listing (upload timestamps make
  ``since`` cheap),
* a club page linking to Sailwave files,
* a single file URL.

Seeds live in :mod:`irc_data.scrapers.sailwave_seeds`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from irc_data.db.result_identity import normalize_key_name as normalize_name
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

logger = logging.getLogger(__name__)

SOURCE = "sailwave"

# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------

STATUS_CODES = (
    "DNC", "DNS", "DNF", "RET", "DSQ", "DNE", "DGM", "OCS", "BFD", "UFD", "NSC",
    "ZFP", "SCP", "RDG", "RAF", "DPI", "TLE", "STP", "NCS",
)
_STATUS_RE = re.compile(r"\b(" + "|".join(STATUS_CODES) + r")\b", re.IGNORECASE)
#: codes for which a points value is not a finishing place
_NON_FINISH = {"DNC", "DNS", "DNF", "RET", "DSQ", "DNE", "DGM", "OCS", "BFD", "UFD",
               "NSC", "RAF", "TLE", "NCS"}

_OTHER_SYSTEMS_RE = re.compile(
    r"(?<![A-Z])(ECHO|PY|NHC|PHRF|YTC|CHS|VPRS|ORC|ORCI|ORCC|HPR|SCHS|"
    r"PERSONAL|LEVEL|ONE.?DESIGN|PURSUIT|POC|RCHS|MOCRA|SB20|J/?70|J/?80|SONAR)(?![A-Z])",
    re.IGNORECASE,
)
_IRC_RE = re.compile(r"(?<![A-Z])IRC", re.IGNORECASE)


def _clean(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _cell(td: Tag | None) -> str:
    return _clean(td.get_text(" ")) if td is not None else ""


def parse_tcc(val: str | None) -> Decimal | None:
    """A plausible IRC TCC (0.5 <= x < 2.0); Sailwave uses 2.000 as a placeholder."""
    if not val:
        return None
    try:
        d = Decimal(_clean(val).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return d if Decimal("0.5") <= d < Decimal("2.0") else None


def parse_duration(val: str | None) -> str | None:
    """'0.48.00' / '1:02:03' / '1:02:03:04' (d:h:m:s) / '48:00' -> 'H:MM:SS'."""
    v = _clean(val)
    if not v or _STATUS_RE.search(v):
        return None
    parts = re.split(r"[.:]", v)
    if not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 4:
        d, h, m, s = nums
        h += d * 24
    elif len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, (m, s) = 0, nums
    else:
        return None
    if m >= 60 or s >= 60 or (h == m == s == 0):
        return None
    return f"{h}:{m:02d}:{s:02d}"


def find_status(*texts: str | None) -> str | None:
    for t in texts:
        if t:
            m = _STATUS_RE.search(t)
            if m:
                return m.group(1).upper()
    return None


def _int_place(val: str | None) -> int | None:
    m = re.match(r"^\s*(\d+)", _clean(val))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS = ("january february march april may june july august september "
           "october november december").split()
_WEEKDAY_RE = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\b\.?,?", re.I)


def _month(tok: str) -> int | None:
    tok = tok.lower().rstrip(".")
    for i, m in enumerate(_MONTHS, 1):
        if len(tok) >= 3 and m.startswith(tok):
            return i
    return None


def parse_race_date(text: str | None, ref: date | None, *, roll_back: bool = True) -> date | None:
    """Parse the date forms Sailwave headers use.

    ``ref`` (the "results as of" date, or a year end) supplies the year for
    yearless dates such as "April 18"; a date that would fall more than a
    week after ``ref`` is taken to be the previous year (winter series), or
    rejected (None) with ``roll_back=False`` (an event named for one year).
    """
    t = _clean(text)
    if not t:
        return None
    t = _WEEKDAY_RE.sub(" ", t)
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t, flags=re.I)
    t = _clean(t)
    # ISO
    m = re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # d/m/y (UK)
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", t)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        return _mk(y, int(m.group(2)), int(m.group(1)))
    # d/m (UK, no year) e.g. "15/10" in Parkstone's race titles
    m = re.search(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", t)
    if m and ref is not None:
        return _year_for(ref, int(m.group(2)), int(m.group(1)), roll_back)
    # d Month [y] / Month d[,] [y]
    m = re.search(r"\b(\d{1,2})[\s-]+([A-Za-z]{3,})\.?(?:[\s,-]+(\d{2,4}))?\b", t)
    order = "dm"
    if not m or not _month(m.group(2)):
        m = re.search(r"\b([A-Za-z]{3,})\.?[\s-]+(\d{1,2})(?:[\s,-]+(\d{4}))?\b", t)
        order = "md"
    if not m:
        return None
    if order == "dm":
        day, mon = int(m.group(1)), _month(m.group(2))
    else:
        mon, day = _month(m.group(1)), int(m.group(2))
    if not mon:
        return None
    if m.group(3):
        y = int(m.group(3))
        return _mk(y + 2000 if y < 100 else y, mon, day)
    if ref is None:
        return None
    return _year_for(ref, mon, day, roll_back)


def _year_for(ref: date, mon: int, day: int, roll_back: bool) -> date | None:
    """Put a yearless day/month in ref's year. Sailwave stamps "as of" when it
    publishes, so a race cannot be after it: a later date ("December 13" read
    on 20 January) is the previous year for a winter series, or rejected for
    an event named for a single year (a stale file under a new name)."""
    d = _mk(ref.year, mon, day)
    if d and d > ref + timedelta(days=2):
        d = _mk(ref.year - 1, mon, day) if roll_back else None
    return d


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Page model
# ---------------------------------------------------------------------------

_HEADER_FIELDS = {
    "rank": "rank", "pos": "rank", "position": "rank", "place": "rank", "posn": "rank",
    "fleet": "fleet", "division": "fleet", "div": "fleet",
    "boat": "boat", "boat name": "boat", "boatname": "boat", "yacht": "boat",
    "yacht name": "boat", "name": "boat",
    "sailno": "sail", "sail no": "sail", "sail no.": "sail", "sail number": "sail",
    "sail": "sail", "sail #": "sail", "bow/sail": "sail",
    "class": "design", "type": "design", "boat type": "design", "design": "design",
    "boat class": "design", "make/model": "design",
    "helm": "helm", "helmname": "helm", "helm name": "helm", "skipper": "helm",
    "owner": "helm", "skipper/owner": "helm", "helm/owner": "helm",
    "club": "club",
    "rating": "rating", "handicap": "rating", "hcap": "rating", "tcf": "rating",
    "tcc": "rating_irc", "irc": "rating_irc", "irc tcc": "rating_irc",
    "irc rating": "rating_irc", "irc tcf": "rating_irc", "irc hcap": "rating_irc",
    "echo": "rating_other", "py": "rating_other", "nhc": "rating_other",
    "echo rating": "rating_other", "echo hcap": "rating_other",
    "start": "start", "finish": "finish", "finish time": "finish",
    "elapsed": "elapsed", "elapsed time": "elapsed", "elapsed2": "elapsed",
    "corrected": "corrected", "corrected time": "corrected", "corr": "corrected",
    "corrected2": "corrected",
    "points": "points", "pts": "points",
    "total": "total", "nett": "nett", "net": "nett",
}

_RACE_COL_RE = re.compile(r"^(R|Race)\s*(\d+)\b", re.IGNORECASE)


def _header_field(text: str) -> str | None:
    h = re.sub(r"\{[^}]*\}", "", _clean(text).lower()).strip()
    return _HEADER_FIELDS.get(h)


@dataclass
class RaceColumn:
    index: int
    label: str
    number: int | None
    date_text: str


@dataclass
class SailwaveTable:
    kind: str                      # "race" | "summary"
    fleet: str                     # from the h3 title ("IRC 1", "IRC 1 Champs")
    title: str
    caption: str
    headers: list[str]
    fields: dict[str, int]         # canonical field -> column index
    rows: list[list[str]]
    race_label: str | None = None  # race tables
    race_number: int | None = None
    race_date_text: str | None = None
    race_columns: list[RaceColumn] = field(default_factory=list)  # summary tables

    @property
    def rating_system(self) -> str | None:
        m = re.search(r"Rating system:\s*([A-Za-z0-9 /]+?)(?:,|$)", self.caption)
        return _clean(m.group(1)).upper() if m else None


@dataclass
class SailwavePage:
    title: str
    event: str
    venue: str
    as_of: date | None
    version: str | None
    tables: list[SailwaveTable]

    @property
    def roll_back(self) -> bool:
        """Yearless dates after the as-of date roll back a year only when the
        event is not named for a single year (winter series "2026-27")."""
        years = set(re.findall(r"(?<!\d)(20\d\d)(?!\d)", f"{self.event} {self.title}"))
        return not (self.as_of and years == {str(self.as_of.year)})

    def date_for(self, text: str | None, url: str = "") -> date | None:
        d = parse_race_date(text, self.reference_date(url), roll_back=self.roll_back)
        years = {int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", self.event)}
        if d and len(years) == 1:
            y = next(iter(years))
            wrap = (d.year == y + 1 and d.month <= 3) or (d.year == y - 1 and d.month >= 10)
            if d.year != y and not wrap:
                # A typo'd year in one header ("8th May 2024" in a 2025 series).
                d = _mk(y, d.month, d.day)
        return d

    def reference_date(self, url: str = "") -> date | None:
        if self.as_of:
            return self.as_of
        for text in (self.event, self.title, url):
            m = re.search(r"(?<!\d)(20\d\d)(?!\d)", text or "")
            if m:
                return date(int(m.group(1)), 12, 31)
        m = re.search(r"(?<![\d])(\d\d)(?![\d])", urlsplit(url).path.rsplit("/", 1)[-1]) if url else None
        if m and 15 <= int(m.group(1)) <= 40:
            return date(2000 + int(m.group(1)), 12, 31)
        return None


def _title_before(table: Tag) -> Tag | None:
    prev = table
    for _ in range(12):
        prev = prev.find_previous(["h3", "h2", "h1"])
        if prev is None:
            return None
        cls = " ".join(prev.get("class") or [])
        if prev.name == "h3" and ("seriestitle" in cls or "codestitle" in cls):
            continue
        return prev
    return None


def _caption_after(title: Tag | None, table: Tag) -> str:
    cap = table.find("caption")
    if cap is not None:
        return _cell(cap)
    if title is None:
        return ""
    sib = title.find_next_sibling()
    while sib is not None and sib is not table:
        if sib.name == "div" and "caption" in " ".join(sib.get("class") or []):
            return _cell(sib)
        if sib.name == "table":
            break
        sib = sib.find_next_sibling()
    return ""


_LABEL_RE = re.compile(r"\b(R|Race)\s*(\d+)\b", re.IGNORECASE)
_START_AT_RE = re.compile(r"\s+at\s+\d{1,2}[:.]\d{2}(?::\d{2})?\s*$", re.IGNORECASE)


def _renumber(cols: list) -> None:
    """Series race numbers for summary columns.

    When every column has a distinct R-number it is kept; otherwise (no
    label, or per-day numbering like '15/10 R1', '05/11 R1') the columns are
    numbered in order, which is how Sailwave lays races out.
    """
    nums = [c.number for c in cols]
    if None in nums or len(set(nums)) != len(nums):
        for i, c in enumerate(cols, 1):
            c.number = i


def _renumber_race_tables(tabs: list) -> None:
    """Same rule for race tables, per fleet, keeping the title as race_name."""
    by_fleet: dict[str, list] = {}
    for t in tabs:
        by_fleet.setdefault(_fleet_key(t.fleet), []).append(t)
    for group in by_fleet.values():
        nums = [t.race_number for t in group]
        if None in nums or len(set(nums)) != len(nums):
            for i, t in enumerate(group, 1):
                label = " ".join(x for x in (t.race_label, t.race_date_text) if x)
                t.race_label = label or f"R{i}"
                t.race_number = i


def _split_race_title(text: str) -> tuple[str, str, str]:
    """Race title -> (race label, fleet, date text).

    Sailwave layouts seen: 'R1 - IRC 1 Champs - April 18', 'Race 1 - IRC 1
    Class - 17/04/21', 'Class 1 Fleet - R2 06/10', '1 Class at 10:05 - 29/10'.
    """
    parts = [p.strip() for p in re.split(r"\s+-\s+", _clean(text)) if p.strip()]
    label = date_text = ""
    fleet_parts = []
    probe = date(2000, 12, 31)
    for i, part in enumerate(parts):
        lm = _LABEL_RE.search(part)
        has_date = parse_race_date(part, probe) is not None
        if lm and not label and (has_date or re.fullmatch(r"(R|Race)\s*\d+", part, re.I) or i == 0):
            label = lm.group(0)
            rest = _clean(part.replace(lm.group(0), " "))
            if rest and parse_race_date(rest, probe):
                date_text = rest
            elif rest and i != 0:
                fleet_parts.append(rest)
            continue
        if has_date and (i > 0 or len(part) <= 14):
            if not date_text:
                date_text = part
            continue  # a second rendering of the date ("06-Aug - ... - 06/08/2018")
        fleet_parts.append(part)
    fleet = _START_AT_RE.sub("", " - ".join(fleet_parts)).strip()
    return label, fleet, date_text


def parse_page(html: str) -> SailwavePage:
    soup = BeautifulSoup(html, "html.parser")
    title = _cell(soup.find("title"))
    h1 = soup.find("h1")
    h2 = soup.find("h2")
    as_of = None
    st = soup.find("h3", class_="seriestitle")
    if st is not None:
        m = re.search(r"as of\s+[\d:.]+\s+on\s+(.+)$", _cell(st), re.I)
        if m:
            as_of = parse_race_date(m.group(1), None)
    text = soup.get_text(" ")
    vm = re.search(r"Sailwave Scoring Software\s+([\d.]+)", text)

    tables: list[SailwaveTable] = []
    for t in soup.find_all("table"):
        cls = " ".join(t.get("class") or [])
        if "codestable" in cls:
            continue
        header_row = None
        thead = t.find("thead")
        if thead is not None:
            header_row = thead.find("tr")
        if header_row is None:
            header_row = t.find("tr", class_="titlerow") or t.find("tr")
        if header_row is None:
            continue
        hcells = header_row.find_all(["th", "td"])
        headers_parts = [[_clean(s) for s in c.stripped_strings] for c in hcells]
        headers = [" ".join(p) for p in headers_parts]
        fields: dict[str, int] = {}
        race_cols: list[RaceColumn] = []
        for i, parts in enumerate(headers_parts):
            first = parts[0] if parts else ""
            f = _header_field(first) or _header_field(" ".join(parts))
            if f and f not in fields:
                fields[f] = i
                continue
            if f:
                continue
            joined = " ".join(parts)
            m = _LABEL_RE.search(joined)
            date_like = parse_race_date(joined, date(2000, 12, 31)) is not None
            if (m and _RACE_COL_RE.match(first)) or (date_like and fields):
                label = m.group(0) if m else ""
                date_text = _clean(joined.replace(label, " ")) if label else joined
                race_cols.append(RaceColumn(i, joined if not label else label,
                                            int(m.group(2)) if m else None, date_text))
        if "boat" not in fields and "sail" not in fields:
            continue
        kind = None
        if "racetable" in cls or "elapsed" in fields or "corrected" in fields or "finish" in fields:
            kind = "race"
        elif "summarytable" in cls or race_cols or "nett" in fields:
            kind = "summary"
        if kind is None:
            continue
        title_tag = _title_before(t)
        title_text = _cell(title_tag) if title_tag is not None else ""
        caption = _caption_after(title_tag, t)
        rows = []
        for tr in t.find_all("tr"):
            if tr is header_row or tr.find("th") is not None:
                continue
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            rows.append([_cell(td) for td in tds])
        tab = SailwaveTable(kind=kind, fleet="", title=title_text, caption=caption,
                            headers=headers, fields=fields, rows=rows, race_columns=race_cols)
        if kind == "race":
            label, fleet, date_text = _split_race_title(title_text)
            tab.race_label = label or None
            m = re.search(r"(\d+)", label or "")
            tab.race_number = int(m.group(1)) if m else None
            tab.race_date_text = date_text or None
            tab.fleet = fleet
        else:
            tab.fleet = _START_AT_RE.sub("", title_text).strip()
            _renumber(tab.race_columns)
        tables.append(tab)

    _renumber_race_tables([t for t in tables if t.kind == "race"])
    return SailwavePage(
        title=title,
        event=_cell(h1) or title,
        venue=_cell(h2),
        as_of=as_of,
        version=vm.group(1) if vm else None,
        tables=tables,
    )


# ---------------------------------------------------------------------------
# IRC classification
# ---------------------------------------------------------------------------


def event_names_other_system(event: str) -> bool:
    """An event title naming another handicap system and not IRC."""
    text = _clean(event)
    return bool(_OTHER_SYSTEMS_RE.search(text)) and not _IRC_RE.search(text)


def fleet_is_irc(fleet: str, rating_system: str | None, rating_header: str | None,
                 *, assume_irc: bool = False) -> bool:
    if rating_header == "rating_other":
        return False
    name = _clean(fleet)
    if _OTHER_SYSTEMS_RE.search(_IRC_RE.sub(" ", name)):
        # "Class 1 VPRS Fleet" scored with Sailwave's "IRC" system, or a
        # combined "IRC/ECHO" fleet: not an IRC result.
        return False
    if rating_system:
        if rating_system.startswith("IRC"):
            return True
        if _OTHER_SYSTEMS_RE.search(rating_system) or re.search(r"PORTSMOUTH|YARDSTICK", rating_system):
            return False
        # Generic Sailwave systems ("TCF", "Rating", "Handicap"): decide by name.
    if rating_header == "rating_irc":
        return True
    name = _clean(fleet)
    if _IRC_RE.search(name):
        stripped = _IRC_RE.sub(" ", name)
        return not _OTHER_SYSTEMS_RE.search(stripped)
    if _OTHER_SYSTEMS_RE.search(name):
        return False
    return assume_irc


def _rating_col(tab: SailwaveTable) -> tuple[int | None, str | None]:
    for key in ("rating_irc", "rating", "rating_other"):
        if key in tab.fields:
            return tab.fields[key], key
    return None, None


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def parse_sailwave_html(
    html: str,
    source_url: str = "",
    event_name: str = "",
    organizing_club: str | None = None,
    event_type: str | None = None,
    event_date: date | None = None,  # kept for API compatibility; race dates win
    *,
    event_series: str | None = None,
    assume_irc: bool = False,
    include_summary: bool = True,
    race_dates: dict | None = None,
) -> list[NormalizedResult]:
    """IRC per-race rows from one Sailwave HTML page (see module docstring).

    ``race_dates`` ({(fleet_key, race_no): date}, built by
    :func:`page_race_dates` over the other files of the event) dates race
    tables whose title carries no date; races still undated are skipped.
    """
    page = parse_page(html)
    ref = (page, race_dates if race_dates is not None else merge_race_dates([page_race_dates(page, source_url)]))
    name = event_name or page.event or "Sailwave event"
    club = organizing_club or page.venue or None
    if event_names_other_system(page.event or page.title):
        # "Parkstone Cruiser Winter Series VPRS Overall 2018": the club scored
        # VPRS with Sailwave's IRC rating system; not IRC results.
        return []

    # Rating system per fleet, from the summary captions (race tables have none).
    systems: dict[str, str] = {}
    for tab in page.tables:
        if tab.kind == "summary" and tab.rating_system:
            systems[_fleet_key(tab.fleet)] = tab.rating_system

    out: list[NormalizedResult] = []
    raced: set[tuple[str, int | None]] = set()
    for tab in (t for t in page.tables if t.kind == "race"):
        rows = _race_rows(tab, page, ref, systems, source_url, name, club, event_type,
                          event_series, assume_irc)
        out.extend(rows)
        for r in rows:
            raced.add((_fleet_key(r.class_name or ""), r.race_number))
    if include_summary:
        for tab in (t for t in page.tables if t.kind == "summary"):
            for r in _summary_rows(tab, page, ref, systems, source_url, name, club,
                                   event_type, event_series, assume_irc):
                if (_fleet_key(r.class_name or ""), r.race_number) not in raced:
                    out.append(r)
    return out


def _fleet_key(fleet: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fleet.lower())


def _row_fleet(tab: SailwaveTable, cells: list[str]) -> str:
    """The row's scoring fleet: the table title, unless one table holds
    several fleets (a Fleet/Division column with differing values)."""
    idx = tab.fields.get("fleet")
    if idx is not None and idx < len(cells) and cells[idx]:
        values = {r[idx] for r in tab.rows if idx < len(r) and r[idx]}
        if len(values) > 1 or not tab.fleet:
            return cells[idx]
    return tab.fleet


def _irc_row(tab: SailwaveTable, cells: list[str], systems: dict, rating_key: str | None,
             assume_irc: bool) -> str | None:
    """Fleet name when this row is scored under IRC, else None.

    Both the table's own title (the scoring unit) and the row's fleet must
    be IRC: a "P40 Extract" sub-scoring of boats from the IRC 1 fleet is
    re-ranked within the P40s and is not an IRC fleet result.
    """
    fleet = _row_fleet(tab, cells)
    if tab.fleet and not fleet_is_irc(
        tab.fleet, tab.rating_system or systems.get(_fleet_key(tab.fleet)), rating_key,
        assume_irc=assume_irc,
    ):
        return None
    idx = tab.fields.get("fleet")
    row_fleet = cells[idx] if idx is not None and idx < len(cells) else ""
    if row_fleet and row_fleet != tab.fleet and not fleet_is_irc(
        row_fleet, systems.get(_fleet_key(row_fleet)), rating_key, assume_irc=assume_irc or bool(tab.fleet),
    ):
        return None
    return fleet


def _get(cells: list[str], idx: int | None) -> str:
    return cells[idx] if idx is not None and idx < len(cells) else ""


def page_race_dates(page: SailwavePage, url: str = "") -> dict:
    """Race dates a page states: summary column headers and dated race titles."""
    out: dict = {}
    for tab in page.tables:
        if tab.kind == "summary":
            for rc in tab.race_columns:
                d = page.date_for(rc.date_text, url)
                if d and rc.number is not None:
                    out[(_fleet_key(tab.fleet), rc.number)] = d
        elif tab.race_number is not None:
            d = page.date_for(tab.race_date_text, url)
            if d:
                out[(_fleet_key(tab.fleet), tab.race_number)] = d
    return out


def merge_race_dates(maps) -> dict:
    """Combine per-page maps keyed by (fleet, race number).

    There is deliberately no fleet-less fallback: sub-scorings ("P40
    extract") renumber races, so race 7 of another table says nothing about
    race 7 of an IRC fleet. A race with no date for its own fleet is skipped.
    """
    merged: dict = {}
    for m in maps:
        for key, d in m.items():
            merged.setdefault(key, d)
    return merged


def _date_lookup(dates: dict, fleet: str, number: int | None):
    if number is None:
        return None
    return dates.get((_fleet_key(fleet), number))


def _race_rows(tab, page, ref, systems, url, name, club, event_type, series, assume_irc):
    rating_idx, rating_key = _rating_col(tab)
    page, dates = ref
    date_source = "race_title"
    race_date = page.date_for(tab.race_date_text, url)
    if race_date is None:
        date_source = "event_files"
        race_date = _date_lookup(dates, tab.fleet, tab.race_number)
    if race_date is None and page.as_of is not None:
        # A file publishing a single race, stamped "provisional as of <day>",
        # is that race day's results (Warsash: R7 in WSS25_D3_R1, as of 26 April).
        numbers = {t.race_number for t in page.tables if t.kind == "race"}
        if len(numbers) == 1 and not any(t.kind == "summary" for t in page.tables):
            date_source = "results_as_of"
            race_date = page.as_of
    if race_date is None or rating_idx is None:
        return []
    f = tab.fields
    staged = []
    for cells in tab.rows:
        fleet = _irc_row(tab, cells, systems, rating_key, assume_irc)
        if fleet is None:
            continue
        boat = _get(cells, f.get("boat"))
        sail = _get(cells, f.get("sail"))
        if not boat and not sail:
            continue
        status = find_status(_get(cells, f.get("elapsed")), _get(cells, f.get("corrected")),
                             _get(cells, f.get("finish")), _get(cells, f.get("points")),
                             _get(cells, f.get("rank")))
        if status == "DNC":
            continue
        tcc = parse_tcc(_get(cells, rating_idx))
        if tcc is None:
            continue
        elapsed = parse_duration(_get(cells, f.get("elapsed")))
        corrected = parse_duration(_get(cells, f.get("corrected")))
        finished = status is None or status not in _NON_FINISH
        place = _int_place(_get(cells, f.get("rank"))) if finished else None
        staged.append((fleet, cells, boat, sail, status, tcc, elapsed, corrected, place))
    sizes: dict[str, int] = {}
    for s in staged:
        sizes[s[0]] = sizes.get(s[0], 0) + 1
    out = []
    for fleet, cells, boat, sail, status, tcc, elapsed, corrected, place in staged:
        raw = {
            "boat_name": boat or sail,
            "sail_number": sail or None,
            "design": _get(cells, f.get("design")) or None,
            "helm": _get(cells, f.get("helm")) or None,
            "club": _get(cells, f.get("club")) or None,
            "points": _get(cells, f.get("points")) or None,
            "race_date": race_date.isoformat(),
            "race_title": tab.title,
            "date_source": date_source,
            "results_as_of": page.as_of.isoformat() if page.as_of else None,
            "row_origin": "race_table",
            "rating_system": "IRC",
        }
        if status:
            raw["status"] = status
        if page.version:
            raw["sailwave_version"] = page.version
        out.append(NormalizedResult(
            boat_name=boat or sail,
            sail_number=sail or None,
            event_name=name,
            event_date=race_date,
            event_series=series,
            organizing_club=club,
            event_type=event_type,
            race_name=tab.race_label,
            race_number=tab.race_number,
            place=place,
            fleet_size=sizes[fleet],
            class_name=fleet or None,
            class_place=place,
            class_fleet_size=sizes[fleet],
            status=_status_value(status),
            rating_type="irc_tcc",
            rating_value=tcc,
            elapsed_time=elapsed,
            corrected_time=corrected,
            source_url=url,
            raw_data={k: v for k, v in raw.items() if v is not None},
        ))
    return out


def _status_value(code: str | None) -> str:
    if code is None or code not in _NON_FINISH:
        return "finished"
    return code


def _summary_rows(tab, page, ref, systems, url, name, club, event_type, series, assume_irc):
    rating_idx, rating_key = _rating_col(tab)
    if rating_idx is None or not tab.race_columns:
        return []
    f = tab.fields
    # Points are only a finishing place under low-point scoring with 1 point
    # for a win; bonus-point systems (0.75 / 0 for first) are skipped.
    firsts = set()
    for cells in tab.rows:
        for rc in tab.race_columns:
            v = _get(cells, rc.index)
            m = re.match(r"^\(?\s*(\d+(?:\.\d+)?)", v)
            if m and not find_status(v):
                firsts.add(Decimal(m.group(1)))
    if firsts and min(firsts) < 1:
        return []
    out = []
    page, dates = ref
    for rc in tab.race_columns:
        race_date = page.date_for(rc.date_text, url) or _date_lookup(dates, tab.fleet, rc.number)
        if race_date is None:
            continue
        staged = []
        for cells in tab.rows:
            fleet = _irc_row(tab, cells, systems, rating_key, assume_irc)
            if fleet is None:
                continue
            boat = _get(cells, f.get("boat"))
            sail = _get(cells, f.get("sail"))
            v = _get(cells, rc.index)
            if not v or (not boat and not sail):
                continue
            status = find_status(v)
            if status == "DNC":
                continue
            tcc = parse_tcc(_get(cells, rating_idx))
            if tcc is None:
                continue
            m = re.match(r"^\(?\s*(\d+(?:\.\d+)?)", v)
            if not m:
                continue
            # Points equal the finishing place only for a clean finish;
            # penalties (ZFP/SCP/DPI) and redress (RDG) points do not.
            place = int(Decimal(m.group(1))) if status is None else None
            staged.append((fleet, cells, boat, sail, status, tcc, place, v))
        sizes: dict[str, int] = {}
        for s in staged:
            sizes[s[0]] = sizes.get(s[0], 0) + 1
        for fleet, cells, boat, sail, status, tcc, place, v in staged:
            raw = {
                "boat_name": boat or sail,
                "sail_number": sail or None,
                "design": _get(cells, f.get("design")) or None,
                "helm": _get(cells, f.get("helm")) or None,
                "club": _get(cells, f.get("club")) or None,
                "points": v,
                "discarded": v.startswith("(") or None,
                "race_date": race_date.isoformat(),
                "race_title": f"{rc.label} - {fleet} - {rc.date_text}",
                "row_origin": "summary",
                "rating_system": "IRC",
                "series_place": _int_place(_get(cells, f.get("rank"))),
                "series_nett": _get(cells, f.get("nett")) or None,
            }
            if status:
                raw["status"] = status
            out.append(NormalizedResult(
                boat_name=boat or sail,
                sail_number=sail or None,
                event_name=name,
                event_date=race_date,
                event_series=series,
                organizing_club=club,
                event_type=event_type,
                race_name=rc.label,
                race_number=rc.number,
                place=place,
                fleet_size=sizes[fleet],
                class_name=fleet or None,
                class_place=place,
                class_fleet_size=sizes[fleet],
                status=_status_value(status),
                rating_type="irc_tcc",
                rating_value=tcc,
                source_url=url,
                raw_data={k: v2 for k, v2 in raw.items() if v2 is not None},
            ))
    return out


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def parse_folder_index(html: str, base_url: str) -> list[tuple[str, datetime | None]]:
    """sailwave.com/results/<folder>/ listing -> [(file url, uploaded at)].

    Only .htm/.html files; sub-folders are not followed (each club folder is
    its own seed).
    """
    out = []
    seen = set()
    for m in re.finditer(
        r"<p>\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?\s*<a\s+href=\"([^\"]+)\"", html, re.I
    ):
        href = m.group(3)
        if not re.search(r"\.html?$", href, re.I):
            continue
        url = normalise_url(urljoin(base_url, href))
        if url in seen:
            continue
        seen.add(url)
        try:
            ts = datetime.fromisoformat(f"{m.group(1)}T{m.group(2) or '00:00:00'}")
        except ValueError:
            ts = None
        out.append((url, ts))
    # Sub-folders are plain IIS listings:
    # "Sunday, December 29, 2019 10:14 AM   101787 <A HREF="/results/x/results.htm">"
    for m in re.finditer(
        r"[A-Za-z]+,\s+([A-Za-z]+ \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M)\s+\d+\s+<A\s+HREF=\"([^\"]+)\"",
        html, re.I,
    ):
        href = m.group(2)
        if not re.search(r"\.html?$", href, re.I):
            continue
        url = normalise_url(urljoin(base_url, href))
        if url in seen:
            continue
        seen.add(url)
        try:
            ts = datetime.strptime(m.group(1), "%B %d, %Y %I:%M %p")
        except ValueError:
            ts = None
        out.append((url, ts))
    return out


def normalise_url(url: str) -> str:
    """https, percent-encoded path (Sailwave filenames contain spaces)."""
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    path = quote(parts.path, safe="/%~._-()!*'&+,;=:@")
    return urlunsplit((scheme, parts.netloc, path, parts.query, ""))


def discover_sailwave_links(html: str, base_url: str, pattern: str | None = None,
                            pdf: bool = False) -> list[dict]:
    """Links to .htm/.html (and with ``pdf``, .pdf) files on a club results page."""
    soup = BeautifulSoup(html, "html.parser")
    links, seen = [], set()
    ext = r"\.(html?|pdf)($|\?)" if pdf else r"\.html?($|\?)"
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip())
        if not re.search(ext, full, re.I):
            continue
        if pattern and not re.search(pattern, full, re.I):
            continue
        full = normalise_url(full)
        if full in seen:
            continue
        seen.add(full)
        links.append({"url": full, "text": a.get_text(strip=True),
                      "filename": full.rsplit("/", 1)[-1]})
    return links


def is_sailwave_html(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    return ("sailwave scoring software" in low or "sailwave results" in low
            or 'class="summarytable"' in low or 'class="racetable"' in low
            or "summaryrace" in low)


def page_mentions_irc(html: str) -> bool:
    """Cheap pre-filter before a full parse."""
    return bool(re.search(r"(?<![A-Za-z])IRC|TCC", html))


# ---------------------------------------------------------------------------
# Seeds and the RaceResultSource
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SailwaveSeed:
    """One place Sailwave results are published.

    url        folder listing (sailwave.com/results/<club>/), club page, or file
    club       organising club stored on every row
    source     race_results.source / register slug the rows belong to
    series     optional event_series label (e.g. "Warsash Spring Series")
    include    regex a file URL must match (applied to the decoded filename)
    exclude    regex that drops a file
    assume_irc treat fleets with no handicap-system token as IRC (only for
               clubs whose keelboat fleets are IRC-only)
    country    GB / IE (documentation; not stored)
    """

    url: str
    club: str
    source: str = SOURCE
    series: str | None = None
    include: str | None = None
    exclude: str | None = None
    assume_irc: bool = False
    country: str = "GB"
    note: str = ""
    pdf: bool = False  # also follow .pdf links (Sailwave pages printed to PDF)

    @property
    def kind(self) -> str:
        if re.search(r"\.(html?|pdf)$", urlsplit(self.url).path, re.I):
            return "file"
        if re.match(r"https?://(www\.)?sailwave\.com/results/", self.url, re.I) and self.url.endswith("/"):
            return "folder"
        return "page"

    def wants(self, url: str) -> bool:
        from urllib.parse import unquote

        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        if self.include and not re.search(self.include, name, re.I):
            return False
        if self.exclude and re.search(self.exclude, name, re.I):
            return False
        return True


class DiscoveryError(RuntimeError):
    """A seed that must list files listed none (site moved / blocked)."""


class SailwaveSource(RaceResultSource):
    """Discover + scrape Sailwave IRC results for a list of seeds."""

    def __init__(self, seeds, *, source: str = SOURCE, client=None):
        from irc_data.scrapers.polite_http import PoliteClient

        self.seeds = list(seeds)
        self._source = source
        self.client = client or PoliteClient()
        self.seed_errors: list[str] = []
        self.seed_counts: dict[str, int] = {}

    def source_name(self) -> str:
        return self._source

    def _listing(self, seed: SailwaveSeed) -> list[tuple[str, datetime | None]]:
        if seed.kind == "file":
            return [(normalise_url(seed.url), None)]
        resp = self.client.get(seed.url)
        if resp.status_code != 200:
            raise DiscoveryError(f"{seed.url}: HTTP {resp.status_code}")
        if seed.kind == "folder":
            return parse_folder_index(resp.text, str(resp.url))
        return [(l["url"], None) for l in discover_sailwave_links(resp.text, str(resp.url), pdf=seed.pdf)]

    def _last_modified(self, url: str) -> datetime | None:
        try:
            resp = self.client.get(url, method="HEAD")
        except Exception:  # noqa: BLE001
            return None
        lm = resp.headers.get("last-modified")
        if not lm:
            return None
        try:
            return parsedate_to_datetime(lm).replace(tzinfo=None)
        except (TypeError, ValueError):
            return None

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        from irc_data.scrapers.polite_http import SourceBlockedError

        refs: list[EventRef] = []
        seen: set[str] = set()
        for seed in self.seeds:
            try:
                listing = self._listing(seed)
            except SourceBlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                self.seed_errors.append(f"{seed.url}: {type(e).__name__}: {e}"[:300])
                continue
            wanted = [(u, ts) for u, ts in listing if seed.wants(u)]
            self.seed_counts[seed.url] = len(wanted)
            if not wanted:
                self.seed_errors.append(f"{seed.url}: no result files found")
                continue
            for url, ts in wanted:
                if url in seen:
                    continue
                if since is not None:
                    if ts is None and seed.kind != "folder":
                        ts = self._last_modified(url)
                    if ts is not None and ts.date() < since:
                        continue
                seen.add(url)
                refs.append(EventRef(
                    source=seed.source,
                    event_name=url.rsplit("/", 1)[-1],
                    event_url=url,
                    event_date=ts.date() if ts else None,
                    organizing_club=seed.club,
                    metadata={"seed": seed.url, "uploaded": ts.isoformat() if ts else None,
                              "series": seed.series, "assume_irc": seed.assume_irc},
                ))
        return refs

    def fetch_html(self, ref: EventRef) -> str | None:
        """The page's HTML when it is a Sailwave page mentioning IRC, else None."""
        resp = self.client.get(ref.event_url)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        if urlsplit(ref.event_url).path.lower().endswith(".pdf"):
            from irc_data.scrapers.sailwave_pdf import pdf_to_sailwave_html

            return pdf_to_sailwave_html(resp.content)  # None: image-only / not Sailwave
        html = _decode(resp)
        if not is_sailwave_html(html) or not page_mentions_irc(html):
            return None
        return html

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        html = self.fetch_html(ref)
        return self.rows_from(ref, html) if html else []

    def rows_from(self, ref: EventRef, html: str, race_dates: dict | None = None) -> list[NormalizedResult]:
        meta = ref.metadata or {}
        rows = parse_sailwave_html(
            html, source_url=ref.event_url, organizing_club=ref.organizing_club,
            event_series=meta.get("series"), assume_irc=bool(meta.get("assume_irc")),
            race_dates=race_dates,
        )
        for r in rows:
            r.raw_data = dict(r.raw_data or {})
            r.raw_data["seed_url"] = meta.get("seed")
            if meta.get("uploaded"):
                r.raw_data["uploaded"] = meta["uploaded"]
        return rows


def _decode(resp) -> str:
    """Sailwave files are often windows-1252 without a charset header."""
    ctype = resp.headers.get("content-type", "")
    if "charset" in ctype.lower():
        return resp.text
    raw = resp.content
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1252", errors="replace")


#: Back-compat: the register's adapter_class names this.
SailwaveScraper = SailwaveSource


# ---------------------------------------------------------------------------
# Run (scheduled runner + backfill)
# ---------------------------------------------------------------------------


def _race_key(r: NormalizedResult) -> tuple:
    """One race of one fleet of one event. Race numbers are unique within a
    fleet, so the date is not part of the key (an amended re-issue of R9 can
    carry a different "as of" day); unnumbered races fall back to name + date."""
    # The year stays in the key: club series reuse names every season
    # ("Wednesday Evening Series" race 1 of 2024 is not race 1 of 2025).
    base = (r.event_name, _fleet_key(r.class_name or ""), r.event_date.year if r.event_date else None)
    if r.race_number is not None:
        return base + (r.race_number,)
    return base + (r.race_name, r.event_date)


def _physical_key(r: NormalizedResult) -> tuple | None:
    raw = r.raw_data or {}
    if raw.get("row_origin") != "race_table" or not (r.elapsed_time or r.corrected_time):
        return None
    return (r.organizing_club, _fleet_key(r.class_name or ""), r.event_date,
            normalize_name(r.boat_name), normalize_name(r.sail_number), r.elapsed_time, r.corrected_time)


def _rank(r: NormalizedResult) -> tuple:
    raw = r.raw_data or {}
    return (raw.get("uploaded") or "", raw.get("results_as_of") or "", r.source_url or "")


def dedupe_rows(rows: list[NormalizedResult]) -> tuple[list[NormalizedResult], int]:
    """Keep one row per boat per race across files.

    * Race-table rows beat summary rows for the same event/fleet/race.
    * When several files publish the same race (amended re-issues such as
      Warsash "270424-R4a" for R9), the latest file wins (upload time, then
      its "results as of" stamp).
    * If the winner was only dated by its "as of" stamp, the race keeps the
      best date any copy states: a title/event date, else the earliest stamp
      (the race-day publication).
    """
    best: dict[tuple, tuple] = {}
    dates: dict[tuple, list] = {}
    for r in rows:
        raw = r.raw_data or {}
        origin = raw.get("row_origin")
        k = (_race_key(r), origin)
        if k not in best or _rank(r) > best[k]:
            best[k] = _rank(r)
        if origin == "race_table" and r.event_date is not None:
            dates.setdefault(_race_key(r), []).append((raw.get("date_source") != "results_as_of", r.event_date))
    # The same race result published in several series files of one club
    # (Parkstone "IRC Overall" and "IRC Mini Series 1" both list race 1):
    # identical fleet, day, boat and times. Keep the copy from the file that
    # publishes the most rows (the overall series).
    per_file: dict[str, int] = {}
    for r in rows:
        per_file[r.source_url or ""] = per_file.get(r.source_url or "", 0) + 1
    physical: dict[tuple, tuple] = {}
    for r in rows:
        pk = _physical_key(r)
        if pk is not None:
            cand = (per_file[r.source_url or ""], r.source_url or "")
            if pk not in physical or cand > physical[pk]:
                physical[pk] = cand
    out, dropped = [], 0
    for r in rows:
        raw = r.raw_data or {}
        origin = raw.get("row_origin")
        rk = _race_key(r)
        if _rank(r) != best[(rk, origin)]:
            dropped += 1
            continue
        pk = _physical_key(r)
        if pk is not None and physical[pk][1] != (r.source_url or ""):
            dropped += 1
            continue
        if origin == "summary" and (rk, "race_table") in best:
            dropped += 1
            continue
        if origin == "race_table" and raw.get("date_source") == "results_as_of":
            stated = [d for good, d in dates.get(rk, []) if good]
            fixed = min(stated) if stated else min(d for _, d in dates.get(rk, [(False, r.event_date)]))
            if fixed != r.event_date:
                r.event_date = fixed
                r.raw_data = dict(raw, race_date=fixed.isoformat())
        out.append(r)
    return out, dropped


def _drop_races_already_tabled(engine, rows: list[NormalizedResult], source: str):
    """Skip summary rows for races a race table already supplied in an earlier run."""
    from sqlalchemy import text

    summary = [r for r in rows if (r.raw_data or {}).get("row_origin") == "summary"]
    if not summary:
        return rows, 0
    keys = {(r.event_name, r.class_name, r.race_number) for r in summary}
    have = set()
    with engine.connect() as conn:
        for ev, cls, rn in keys:
            hit = conn.execute(text(
                "SELECT 1 FROM race_results WHERE source = :s AND event_name = :e "
                "AND class_name IS NOT DISTINCT FROM :c AND race_number IS NOT DISTINCT FROM :r "
                "AND raw_data->>'row_origin' = 'race_table' LIMIT 1"
            ), {"s": source, "e": ev, "c": cls, "r": rn}).first()
            if hit:
                have.add((ev, cls, rn))
    kept = [r for r in rows if not ((r.raw_data or {}).get("row_origin") == "summary"
                                    and (r.event_name, r.class_name, r.race_number) in have)]
    return kept, len(rows) - len(kept)


def run(record: dict | None = None, *, source: str, seeds, since: date | None = None,
        engine=None, store: bool = True) -> dict[str, Any]:
    """Discover, scrape and persist Sailwave IRC results for ``seeds``.

    Fails loudly when discovery is broken: every seed failing to list files,
    or a seed whose listing came back empty, raises ``DiscoveryError`` (a
    quiet period with no *new* files is fine; an empty listing is not).
    """
    import asyncio

    from irc_data.db.operations import log_ingestion_end, log_ingestion_start
    from irc_data.scrapers.result_import import store_normalized_results

    seeds = list(seeds)
    src = SailwaveSource(seeds, source=source)
    try:
        refs = asyncio.run(src.discover_events(since=since))
        if src.seed_errors and len(src.seed_errors) >= len(seeds):
            raise DiscoveryError(f"{source}: discovery failed for every seed: {src.seed_errors[:3]}")
        empty = [e for e in src.seed_errors if e.endswith("no result files found")]
        from irc_data.scrapers.polite_http import SourceBlockedError

        file_errors: list[str] = []
        pages: list[tuple[EventRef, str, SailwavePage]] = []
        for ref in refs:
            try:
                html = src.fetch_html(ref)
            except SourceBlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                file_errors.append(f"{ref.event_url}: {type(e).__name__}: {e}"[:300])
                continue
            if html:
                pages.append((ref, html, parse_page(html)))
        # Race tables often carry no date ("R9 - IRC 1"); the event's other
        # files (summary column headers, dated race titles) supply it.
        by_event: dict[str, list[dict]] = {}
        for ref, _html, page in pages:
            by_event.setdefault(page.event, []).append(page_race_dates(page, ref.event_url))
        event_dates = {ev: merge_race_dates(maps) for ev, maps in by_event.items()}
        rows: list[NormalizedResult] = []
        files_with_irc = 0
        for ref, html, page in pages:
            got = src.rows_from(ref, html, event_dates.get(page.event))
            if got:
                files_with_irc += 1
                rows.extend(got)
    finally:
        src.client.close()

    rows, dup_dropped = dedupe_rows(rows)
    stats: dict[str, Any] = {
        "records_written": 0, "records_found": len(rows), "files": len(refs),
        "files_with_irc": files_with_irc, "file_errors": len(file_errors),
        "duplicates_dropped": dup_dropped, "seed_errors": src.seed_errors[:20],
        "seed_files": src.seed_counts, "since": since.isoformat() if since else None,
        "source": source, "http_requests": src.client.requests,
    }
    if file_errors:
        stats["file_error_samples"] = file_errors[:10]
    if store and rows:
        if engine is None:
            from irc_data.db.connection import get_engine

            engine = get_engine()
        rows, already = _drop_races_already_tabled(engine, rows, source)
        stats["summary_rows_skipped_tabled"] = already
        log_id = log_ingestion_start(engine, source, metadata={
            "adapter": f"legacy-cli:{source}", "since": stats["since"]})
        try:
            stored = store_normalized_results(engine, rows, source=source, log=False, detail=True)
        except Exception as e:
            log_ingestion_end(engine, log_id, status="failed", records_found=len(rows),
                              records_new=0, error_message=str(e)[:1000])
            raise
        errs = file_errors + stored["error_details"]
        log_ingestion_end(engine, log_id,
                          status="completed" if not errs else "completed_with_errors",
                          records_found=len(rows), records_new=stored["written"],
                          error_message="; ".join(errs)[:1000] if errs else None)
        stats.update(records_written=stored["written"], upserted=stored["imported"],
                     boats_matched_rows=stored["matched"], store_errors=stored["errors"])
    if empty:
        # Raised after storing so what was found is kept, but the run is red.
        raise DiscoveryError(f"{source}: seeds listed no result files: {empty[:5]}; stats={stats}")
    return stats
