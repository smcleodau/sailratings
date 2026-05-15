"""SailRaceHQ (sailracehq.com) race results scraper.

SailRaceHQ is the RORC race management platform, powered by NauticalCloud.
RORC migrated to SailRaceHQ after 2022, so all 2023+ results live here.

The site is a Blazor/Radzen SPA behind Cloudflare challenge protection,
requiring Playwright (headless Chromium) to render pages. No public API.

URL patterns:
  /results?year={YYYY}                  - year listing (events + single races)
  /raceresults/{GUID}                   - single race results (paginated)
  /results/event/{GUID}/{year}          - multi-race event (links to sub-races)
  /regatta-results/{GUID}?year={YYYY}   - series/regatta aggregate

Pagination:
  ?pageSize=100&pageIndex=0   (max 100 per page)
  Default page size is 25

Division/class filtering:
  ?divisionId={GUID}          - IRC, Class 40, IMOCA, etc.
  &class={GUID}               - IRC Overall, IRC Zero, IRC 1, etc.
"""

import asyncio
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from irc_data.scrapers.base import RateLimiter
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

SAILRACEHQ_BASE = "https://sailracehq.com"

# Rate limiter: be gentle — Playwright pages take time anyway
sailracehq_limiter = RateLimiter(min_delay=3.0, jitter=2.0)

# Years available on SailRaceHQ (RORC migrated after 2022)
# Goes up to current year + 1 to catch early-scheduled events
SAILRACEHQ_YEARS = list(range(2023, date.today().year + 1))

# Divisions we care about for IRC data
IRC_DIVISION_KEYWORDS = {"irc"}

# Statuses that indicate non-finish
NON_FINISH_STATUSES = {"DNF", "DNS", "DSQ", "OCS", "RET", "NSC", "DNC", "RAF", "SCP"}

# Page size for results pagination (max supported by SailRaceHQ)
MAX_PAGE_SIZE = 100

# JS extraction functions used by Playwright
_JS_EXTRACT_EVENTS = """() => {
    const results = [];

    // Single-race items: rows with direct /raceresults/ links
    // Multi-race items: rows with /results/event/ links
    // Both use the same row structure: .py-2 or .events-race-item-row

    // Approach: find all anchor tags to raceresults or results/event,
    // then walk up to find the row container with date/name info
    const allLinks = document.querySelectorAll(
        'a[href*="/raceresults/"], a[href*="/results/event/"]'
    );

    const seen = new Set();

    allLinks.forEach(link => {
        const href = link.getAttribute('href');
        if (!href) return;

        // Extract the GUID from the href
        let guid = null;
        let linkType = null;
        const raceMatch = href.match(/\\/raceresults\\/([0-9a-f-]{36})/);
        const eventMatch = href.match(/\\/results\\/event\\/([0-9a-f-]{36})/);
        if (raceMatch) {
            guid = raceMatch[1];
            linkType = 'race';
        } else if (eventMatch) {
            guid = eventMatch[1];
            linkType = 'event';
        }
        if (!guid || seen.has(guid)) return;
        seen.add(guid);

        // Walk up to find the row with date/name context
        let container = link.closest('.events-race-item-row')
                     || link.closest('.py-2')
                     || link.closest('.hover-row');

        let raceName = '';
        let dateStr = '';
        let raceNumber = null;

        if (container) {
            const text = container.innerText.trim();
            const lines = text.split('\\n').map(s => s.trim()).filter(s => s);

            // Parse date: lines typically are [MONTH, DAY, YEAR, DayOfWeek, ...]
            // or [MONTH, DAY, YEAR, "Day • Race #N", ...]
            const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
            for (let i = 0; i < lines.length - 2; i++) {
                if (months.includes(lines[i].toUpperCase())) {
                    const day = lines[i+1];
                    const year = lines[i+2];
                    if (/^\\d{1,2}$/.test(day) && /^\\d{4}$/.test(year)) {
                        dateStr = `${day} ${lines[i]} ${year}`;
                        break;
                    }
                }
            }

            // Parse race name: skip date parts, day-of-week, "Confirmed", "View"
            const skipWords = new Set(months.concat(['CONFIRMED', 'VIEW', 'MON', 'TUE', 'WED',
                'THU', 'FRI', 'SAT', 'SUN']));
            for (const line of lines) {
                const upper = line.toUpperCase();
                if (skipWords.has(upper)) continue;
                if (/^\\d{1,4}$/.test(line)) continue;  // day or year number
                if (line.startsWith('•')) {
                    // "• Race #13" -> extract race number
                    const rn = line.match(/#(\\d+)/);
                    if (rn) raceNumber = parseInt(rn[1]);
                    continue;
                }
                // Check for "Day • Race #N" combined format
                const combined = line.match(/^\\w+\\s*•\\s*Race\\s*#(\\d+)/i);
                if (combined) {
                    raceNumber = parseInt(combined[1]);
                    continue;
                }
                // This is likely the race name
                if (line.length > 3 && !raceName) {
                    raceName = line;
                }
            }
        }

        results.push({
            guid: guid,
            type: linkType,
            url: link.href,
            race_name: raceName,
            date_str: dateStr,
            race_number: raceNumber
        });
    });

    return results;
}"""

_JS_EXTRACT_RACE_META = """() => {
    const lines = document.body.innerText.split('\\n').map(s => s.trim()).filter(s => s);
    let raceName = '';
    let startDate = '';

    for (let i = 0; i < Math.min(lines.length, 40); i++) {
        if (lines[i].startsWith('Start Date:')) {
            startDate = lines[i].replace('Start Date:', '').trim();
        }
        // The race name is typically a prominent line before "Start Date"
        // and after the navigation menu items
        if (!raceName && lines[i].length > 5 && i > 5
            && !['Race Programme', 'Enter Races', 'Race Documents', 'Live Results',
                 'Miles Leaderboard', 'Race Results', 'Crew Match', 'My Account',
                 'Confirmed', 'Search by boat'].some(s => lines[i].includes(s))
            && !/^(Start Date|Replay|View|Elapsed|IRC|Class|IMOCA|Ultim|MOCRA|Ocean|Line|Sector|Clipper)/.test(lines[i])) {
            raceName = lines[i];
        }
    }

    // Get division tabs
    const divTabs = document.querySelectorAll('a[href*="divisionId"]');
    const divisions = Array.from(divTabs).slice(0, 20).map(t => ({
        name: t.innerText.trim(),
        href: t.href
    }));

    // Get total results count
    const totalText = document.body.innerText.match(/Total results for[^:]*:\\s*(\\d+)/);
    const totalResults = totalText ? parseInt(totalText[1]) : null;

    return {
        race_name: raceName,
        start_date: startDate,
        divisions: divisions,
        total_results: totalResults
    };
}"""

_JS_EXTRACT_RESULTS = """() => {
    const cards = document.querySelectorAll('.card-customs-style-1');
    const data = [];

    cards.forEach(card => {
        const entry = {};

        // Position
        const posEl = card.querySelector('.result-no span.h4');
        entry.position = posEl ? posEl.innerText.trim() : null;

        // Boat name
        const boatLink = card.querySelector('a.btn-view-boat');
        entry.boat_name = boatLink ? boatLink.innerText.trim() : null;

        // Sail number
        const boatContainer = boatLink ? boatLink.closest('.row.no-gutters') : null;
        if (boatContainer) {
            const sailEl = boatContainer.querySelector('span[style*="font-size: 12px"]');
            entry.sail_number = sailEl ? sailEl.innerText.trim() : null;
        }

        // Parse structured data from text content using label/value pattern
        const text = card.innerText;
        const lines = text.split('\\n').map(s => s.trim()).filter(s => s);

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const next = i + 1 < lines.length ? lines[i + 1] : '';

            switch (line) {
                case 'TCC':
                    entry.tcc = next;
                    break;
                case 'Elapsed':
                    if (!entry.elapsed) entry.elapsed = next;
                    break;
                case 'Corrected':
                    // "Corrected" label appears twice sometimes (header + value)
                    if (next === 'Corrected' && i + 2 < lines.length) {
                        entry.corrected = lines[i + 2];
                    } else if (next !== 'Corrected') {
                        entry.corrected = next;
                    }
                    break;
                case 'Finished':
                    entry.finish_time = next;
                    if (!entry.status) entry.status = 'Finished';
                    break;
                case 'Skipper/s':
                    entry.skipper = next;
                    break;
                case 'Owner':
                    entry.owner = next;
                    break;
                case 'Boat Type':
                    entry.boat_type = next;
                    break;
                case 'Points':
                    entry.points = next;
                    break;
                case 'Source':
                    entry.data_source = next;
                    break;
                case 'Rating':
                    if (!entry.tcc) entry.tcc = next;
                    break;
            }

            // Check for non-finish statuses
            if (['DNF', 'DNS', 'DSQ', 'OCS', 'RET', 'NSC', 'DNC', 'RAF', 'SCP'].includes(line)) {
                entry.status = line;
            }
        }

        if (entry.boat_name) {
            data.push(entry);
        }
    });

    return data;
}"""

_JS_EXTRACT_EVENT_RACES = """() => {
    // On a multi-race event page, find all sub-race links
    const links = document.querySelectorAll('a[href*="/raceresults/"]');
    const seen = new Set();
    const races = [];

    links.forEach(link => {
        const href = link.getAttribute('href');
        const match = href.match(/\\/raceresults\\/([0-9a-f-]{36})/);
        if (!match || seen.has(match[1])) return;
        seen.add(match[1]);

        // Find the row container for context
        const row = link.closest('.events-race-item-row')
                 || link.closest('.py-2')
                 || link.closest('.hover-row');

        let raceName = '';
        let dateStr = '';
        let raceNumber = null;

        if (row) {
            const text = row.innerText.trim();
            const lines = text.split('\\n').map(s => s.trim()).filter(s => s);

            const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
            for (let i = 0; i < lines.length - 2; i++) {
                if (months.includes(lines[i].toUpperCase())) {
                    dateStr = `${lines[i+1]} ${lines[i]} ${lines[i+2]}`;
                    break;
                }
            }

            const skipWords = new Set(months.concat(['CONFIRMED', 'VIEW', 'MON', 'TUE', 'WED',
                'THU', 'FRI', 'SAT', 'SUN']));
            for (const line of lines) {
                if (skipWords.has(line.toUpperCase())) continue;
                if (/^\\d{1,4}$/.test(line)) continue;
                if (line.startsWith('•')) {
                    const rn = line.match(/#(\\d+)/);
                    if (rn) raceNumber = parseInt(rn[1]);
                    continue;
                }
                const combined = line.match(/^\\w+\\s*•\\s*Race\\s*#(\\d+)/i);
                if (combined) {
                    raceNumber = parseInt(combined[1]);
                    continue;
                }
                if (line.length > 3 && !raceName) {
                    raceName = line;
                }
            }
        }

        races.push({
            guid: match[1],
            url: link.href,
            race_name: raceName,
            date_str: dateStr,
            race_number: raceNumber
        });
    });

    return races;
}"""


def _safe_decimal(val: str | None) -> Decimal | None:
    """Parse a decimal value from string."""
    if not val:
        return None
    val = val.strip().replace("\xa0", "").replace(",", "")
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(date_str: str | None) -> date | None:
    """Parse date from SailRaceHQ format (e.g. '22 Jul 2023' or '22 JUL 2023')."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ["%d %b %Y", "%d %B %Y", "%d %b %y"]:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _parse_elapsed(val: str | None) -> str | None:
    """Parse SailRaceHQ elapsed/corrected time format.

    Formats seen:
      '02d 16:40:02'  -> '64:40:02'  (days + HH:MM:SS)
      '16:40:02'      -> '16:40:02'  (HH:MM:SS)
    """
    if not val:
        return None
    val = val.strip()
    # Format with days: "02d 16:40:02"
    m = re.match(r"(\d+)d\s+(\d+):(\d+):(\d+)", val)
    if m:
        days = int(m.group(1))
        hours = int(m.group(2))
        mins = int(m.group(3))
        secs = int(m.group(4))
        total_hours = days * 24 + hours
        return f"{total_hours}:{mins:02d}:{secs:02d}"
    # Format without days: "16:40:02"
    m = re.match(r"(\d+):(\d+):(\d+)", val)
    if m:
        return val.strip()
    return None


def _detect_status(status_text: str | None) -> str:
    """Map SailRaceHQ status text to normalized status."""
    if not status_text:
        return "finished"
    status_text = status_text.strip().upper()
    if status_text in NON_FINISH_STATUSES:
        return status_text
    if status_text == "FINISHED":
        return "finished"
    return "finished"


def _get_browser_context():
    """Create a Playwright browser context configured to pass Cloudflare.

    Returns (playwright, browser, context) tuple. Caller must close all three.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
    )
    return pw, browser, context


def _pass_cloudflare(page, url: str, wait_seconds: float = 10.0) -> None:
    """Navigate to URL and wait for Cloudflare challenge to resolve."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(wait_seconds)


def _navigate(page, url: str, wait_seconds: float = 6.0) -> None:
    """Navigate to a page after Cloudflare has been passed."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(wait_seconds)


class SailRaceHQSource(RaceResultSource):
    """SailRaceHQ (RORC 2023+) race results source.

    Uses Playwright to render the Blazor SPA and extract race data.
    All methods are synchronous (Playwright's sync API) but wrapped for
    the async interface expected by RaceResultSource.
    """

    def source_name(self) -> str:
        return "sailracehq"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover RORC race results on SailRaceHQ by year."""
        return await asyncio.to_thread(self._discover_events_sync, since)

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape all IRC results from a single race."""
        return await asyncio.to_thread(self._scrape_event_sync, ref)

    def _discover_events_sync(self, since: date | None = None) -> list[EventRef]:
        """Synchronous event discovery using Playwright."""
        min_year = since.year if since else 2023
        events: list[EventRef] = []

        pw, browser, context = _get_browser_context()
        try:
            page = context.new_page()

            # Pass Cloudflare on first load
            print("  Passing Cloudflare challenge...")
            _pass_cloudflare(page, f"{SAILRACEHQ_BASE}/results")

            for year in SAILRACEHQ_YEARS:
                if year < min_year:
                    continue

                print(f"  Scanning {year}...")
                sailracehq_limiter.wait_sync()
                _navigate(page, f"{SAILRACEHQ_BASE}/results?year={year}")

                # Extract event listings from the page
                raw_events = page.evaluate(_JS_EXTRACT_EVENTS)

                year_events = []
                for ev in raw_events:
                    event_date = _parse_date(ev.get("date_str"))
                    race_name = ev.get("race_name", "").strip()
                    race_number = ev.get("race_number")
                    ev_type = ev.get("type")  # 'race' or 'event'

                    if not race_name:
                        race_name = f"RORC Race {ev['guid'][:8]}"

                    full_name = f"{year} RORC - {race_name}"
                    if race_number:
                        full_name += f" (Race #{race_number})"

                    year_events.append(EventRef(
                        source="sailracehq",
                        event_name=full_name,
                        event_url=ev["url"],
                        event_date=event_date,
                        organizing_club="RORC",
                        event_type="offshore",
                        metadata={
                            "year": year,
                            "guid": ev["guid"],
                            "link_type": ev_type,
                            "race_number": race_number,
                        },
                    ))

                events.extend(year_events)
                print(f"    {year}: {len(year_events)} events/races found")

        finally:
            browser.close()
            pw.stop()

        return events

    def _scrape_event_sync(self, ref: EventRef) -> list[NormalizedResult]:
        """Synchronous event scraping using Playwright.

        If the event is a multi-race event (link_type='event'), first
        discovers sub-races, then scrapes each one. If it's a single
        race (link_type='race'), scrapes it directly.
        """
        link_type = ref.metadata.get("link_type", "race") if ref.metadata else "race"

        pw, browser, context = _get_browser_context()
        try:
            page = context.new_page()

            # Pass Cloudflare
            _pass_cloudflare(page, f"{SAILRACEHQ_BASE}/results")

            if link_type == "event":
                return self._scrape_multi_race_event(page, ref)
            else:
                return self._scrape_single_race(page, ref)
        finally:
            browser.close()
            pw.stop()

    def _scrape_multi_race_event(
        self, page, ref: EventRef
    ) -> list[NormalizedResult]:
        """Scrape a multi-race event page, then each sub-race."""
        sailracehq_limiter.wait_sync()
        _navigate(page, ref.event_url)

        sub_races = page.evaluate(_JS_EXTRACT_EVENT_RACES)
        print(f"    Multi-race event: {len(sub_races)} sub-races")

        all_results = []
        for sub in sub_races:
            sub_ref = EventRef(
                source="sailracehq",
                event_name=f"{ref.event_name} - {sub.get('race_name', '')}".strip(" -"),
                event_url=sub["url"],
                event_date=_parse_date(sub.get("date_str")) or ref.event_date,
                organizing_club="RORC",
                event_type="offshore",
                metadata={
                    "guid": sub["guid"],
                    "link_type": "race",
                    "race_number": sub.get("race_number"),
                    "parent_event": ref.event_name,
                },
            )
            results = self._scrape_single_race(page, sub_ref)
            all_results.extend(results)

        return all_results

    def _scrape_single_race(
        self, page, ref: EventRef
    ) -> list[NormalizedResult]:
        """Scrape results from a single race page.

        Handles pagination to get all results. Focuses on IRC division
        results (the default view on most RORC races).
        """
        race_url = ref.event_url
        # Strip returnUrl param and add pagination
        base_url = race_url.split("?")[0]

        sailracehq_limiter.wait_sync()
        _navigate(page, f"{base_url}?pageSize={MAX_PAGE_SIZE}&pageIndex=0")

        # Get race metadata (name, date, divisions, total count)
        meta = page.evaluate(_JS_EXTRACT_RACE_META)
        race_name = meta.get("race_name") or ref.event_name
        race_date = _parse_date(meta.get("start_date")) or ref.event_date
        total_results = meta.get("total_results")

        # Check if this is an IRC race (look at division tabs)
        divisions = meta.get("divisions", [])
        has_irc = any("irc" in d.get("name", "").lower() for d in divisions)

        if not has_irc and divisions:
            # No IRC division — skip non-IRC races
            div_names = [d.get("name", "") for d in divisions]
            print(f"      Skipping non-IRC race ({', '.join(div_names[:3])})")
            return []

        # Extract first page of results
        all_raw = page.evaluate(_JS_EXTRACT_RESULTS)
        page_index = 0

        # Paginate through remaining results
        if total_results and total_results > MAX_PAGE_SIZE:
            total_pages = (total_results + MAX_PAGE_SIZE - 1) // MAX_PAGE_SIZE
            for page_index in range(1, total_pages):
                sailracehq_limiter.wait_sync()
                _navigate(
                    page,
                    f"{base_url}?pageSize={MAX_PAGE_SIZE}&pageIndex={page_index}",
                )
                page_results = page.evaluate(_JS_EXTRACT_RESULTS)
                all_raw.extend(page_results)
                if not page_results:
                    break  # No more results

        # Convert raw extracted data to NormalizedResult
        results = []
        fleet_size = total_results or len(all_raw)

        for raw in all_raw:
            boat_name = raw.get("boat_name")
            if not boat_name:
                continue

            sail_number = raw.get("sail_number")
            tcc = _safe_decimal(raw.get("tcc"))
            elapsed = _parse_elapsed(raw.get("elapsed"))
            corrected = _parse_elapsed(raw.get("corrected"))
            status = _detect_status(raw.get("status"))

            place = None
            pos_str = raw.get("position")
            if pos_str:
                try:
                    place = int(re.sub(r"[^\d]", "", pos_str))
                except (ValueError, TypeError):
                    pass

            results.append(NormalizedResult(
                boat_name=boat_name,
                sail_number=sail_number,
                event_name=race_name,
                event_date=race_date,
                event_series=ref.metadata.get("parent_event") if ref.metadata else None,
                organizing_club="RORC",
                event_type="offshore",
                race_name=ref.event_name,
                race_number=ref.metadata.get("race_number") if ref.metadata else None,
                place=place,
                fleet_size=fleet_size,
                status=status,
                rating_type="irc_tcc" if tcc else None,
                rating_value=tcc,
                elapsed_time=elapsed,
                corrected_time=corrected,
                source_url=f"{base_url}",
                raw_data={
                    "boat_type": raw.get("boat_type"),
                    "owner": raw.get("owner"),
                    "skipper": raw.get("skipper"),
                    "points": raw.get("points"),
                    "data_source": raw.get("data_source"),
                    "finish_time": raw.get("finish_time"),
                    "status_raw": raw.get("status"),
                },
            ))

        return results


def scrape_sailracehq_results(
    year: int | None = None,
    since: date | None = None,
    max_events: int | None = None,
) -> list[NormalizedResult]:
    """Convenience function to scrape SailRaceHQ RORC results.

    Args:
        year: Specific year to scrape. If None, scrapes all years from 2023.
        since: Only scrape events after this date.
        max_events: Limit number of events to scrape (for testing).

    Returns:
        List of NormalizedResult objects.
    """
    source = SailRaceHQSource()

    if year:
        since_date = date(year, 1, 1)
    elif since:
        since_date = since
    else:
        since_date = date(2023, 1, 1)

    print(f"Discovering SailRaceHQ events (since {since_date})...")
    events = asyncio.run(source.discover_events(since=since_date))

    # Filter to only single races for direct scraping, and expand multi-race events
    if max_events:
        events = events[:max_events]

    print(f"Found {len(events)} events to scrape")

    all_results = []
    for i, event in enumerate(events):
        print(f"\n  [{i + 1}/{len(events)}] {event.event_name}")
        try:
            results = asyncio.run(source.scrape_event(event))
            if results:
                irc_count = sum(1 for r in results if r.rating_value)
                all_results.extend(results)
                print(f"    -> {len(results)} results ({irc_count} with IRC TCC)")
        except Exception as e:
            print(f"    -> Error: {e}")

    return all_results
