"""CLI for IRC data collection and analysis."""

import shutil
from datetime import date
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from sqlalchemy import text

from irc_data.config import IMPORTS_DIR, TCC_LISTINGS_DIR
from irc_data.db.connection import get_engine, init_db
from irc_data.db.operations import (
    find_boat_by_sail_number,
    get_boat_detail,
    get_stats,
    list_boats,
    upsert_boat,
    upsert_tcc_snapshot,
)
from irc_data.parsers.tcc_csv import detect_country, detect_design, parse_tcc_csv

console = Console()


@click.group()
@click.option("--db-url", envvar="IRC_DATABASE_URL", default=None, help="Database URL")
@click.pass_context
def cli(ctx, db_url):
    """IRC sailing data collection & analysis."""
    ctx.ensure_object(dict)
    if db_url:
        ctx.obj["engine"] = get_engine(db_url)
    else:
        ctx.obj["engine"] = get_engine()


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize the database schema."""
    engine = ctx.obj["engine"]
    init_db()
    console.print("[green]Database initialized.[/green]")
    stats = get_stats(engine)
    console.print(f"  Boats: {stats['boats']}, Snapshots: {stats['snapshots']}")


@cli.command(name="import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--snapshot-date", default=str(date.today()), help="Date for this snapshot")
@click.pass_context
def import_csv(ctx, path: Path, snapshot_date: str):
    """Import a TCC listing CSV into the database."""
    engine = ctx.obj["engine"]
    snap_date = date.fromisoformat(snapshot_date)

    # Copy to archive locations
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    TCC_LISTINGS_DIR.mkdir(parents=True, exist_ok=True)

    import_dest = IMPORTS_DIR / path.name
    if not import_dest.exists():
        shutil.copy2(path, import_dest)
        console.print(f"Copied to {import_dest}")

    listing_dest = TCC_LISTINGS_DIR / f"tcc_listing_{snap_date}.csv"
    if not listing_dest.exists():
        shutil.copy2(path, listing_dest)
        console.print(f"Archived as {listing_dest}")

    # Parse
    console.print(f"Parsing {path.name}...")
    rows = parse_tcc_csv(path)
    console.print(f"  Parsed {len(rows)} rows")

    # Import in two passes so primary rows always land before secondaries.
    # Otherwise a secondary that arrives before its primary would be skipped
    # (boat doesn't yet exist) or trigger a stub-insert race condition.
    primary_rows = [r for r in rows if not r.is_secondary]
    secondary_rows = [r for r in rows if r.is_secondary]
    if secondary_rows:
        console.print(
            f"  {len(secondary_rows)} secondary-cert row(s) — will attach to "
            f"existing boats by sail_number (no duplicate boat rows created)."
        )

    imported = 0
    sec_attached = 0
    sec_skipped_no_primary = 0
    with console.status("Importing boats...") as status:
        # ── Pass 1: primary rows — create / update boats + tcc_snapshots
        for row in primary_rows:
            country = detect_country(row.sail_number)
            design = detect_design(row.lh, row.beam)

            boat_id = upsert_boat(
                engine,
                boat_name=row.boat_name,
                sail_number=row.sail_number,
                cert_number=row.cert_number,
                design=design,
                country=country,
                year_built=row.series_date,
            )

            snapshot_fields = {
                "cert_year": row.cert_year,
                "tcc": row.tcc,
                "non_spi_tcc": row.non_spi_tcc,
                "endorsed": row.endorsed,
                "secondary": row.secondary,
                "crew": row.crew,
                "dlr": row.dlr,
                "lh": row.lh,
                "beam": row.beam,
                "draft": row.draft,
                "single_furling_headsail": row.single_furling_headsail,
                "headsails": row.headsails,
                "flying_headsails": row.flying_headsails,
                "spinnakers": row.spinnakers,
                "series_date": row.series_date,
                "age_date": row.age_date,
                "racing_area": row.racing_area,
                "ssb_base_value": row.ssb_base_value,
                "stix": row.stix,
                "avs": row.avs,
                "category": row.category,
            }
            upsert_tcc_snapshot(engine, boat_id, snap_date, **snapshot_fields)
            imported += 1

            if imported % 200 == 0:
                status.update(f"Importing boats... {imported}/{len(primary_rows)}")

        # ── Pass 2: secondary rows — attach to existing boat by
        # (sail_number, boat_name). Matching on sail_number alone is unsafe
        # because sail numbers are legitimately reused across design classes
        # (1,265 such cases in the boats table). The cleaned boat_name plus
        # sail_number is the strongest signal short of cert_number.
        for row in secondary_rows:
            with engine.begin() as conn:
                lookup = conn.execute(text("""
                    SELECT id FROM boats
                     WHERE sail_number = :sn
                       AND UPPER(TRIM(boat_name)) = UPPER(TRIM(:bn))
                     LIMIT 1
                """), {"sn": row.sail_number, "bn": row.boat_name}).first()
                if lookup is None:
                    sec_skipped_no_primary += 1
                    continue
                # The primary snapshot for this date already exists; UPDATE
                # the `secondary` flag without overwriting primary TCC.
                conn.execute(text("""
                    UPDATE tcc_snapshots
                       SET secondary = COALESCE(:flag, secondary)
                     WHERE boat_id = :bid AND snapshot_date = :sd
                """), {
                    "flag": row.secondary or "SEC",
                    "bid": lookup.id,
                    "sd": snap_date,
                })
            sec_attached += 1

    msg = f"[green]Imported {imported} primary boat(s) + snapshots.[/green]"
    if sec_attached or sec_skipped_no_primary:
        msg += (
            f"  Secondary certs: {sec_attached} attached"
            + (f", {sec_skipped_no_primary} skipped (no primary boat found)" if sec_skipped_no_primary else "")
            + "."
        )
    console.print(msg)
    stats = get_stats(engine)
    console.print(
        f"  Total: {stats['boats']} boats, {stats['countries']} countries, "
        f"{stats['designs']} detected designs"
    )


@cli.command()
@click.option("--country", "-c", help="Filter by country code (e.g. AUS, GBR)")
@click.option("--design", "-d", help="Filter by design (e.g. 'Sunfast 3300')")
@click.option("--limit", "-n", default=50, help="Max rows to display")
@click.pass_context
def list(ctx, country, design, limit):
    """List boats with optional filters."""
    engine = ctx.obj["engine"]
    boats = list_boats(engine, country=country, design=design)

    if not boats:
        console.print("[yellow]No boats found.[/yellow]")
        return

    table = Table(title=f"IRC Boats ({len(boats)} total)")
    table.add_column("Boat Name", style="bold")
    table.add_column("Sail No")
    table.add_column("TCC", justify="right")
    table.add_column("Non-Spi", justify="right")
    table.add_column("DLR", justify="right")
    table.add_column("LH", justify="right")
    table.add_column("Beam", justify="right")
    table.add_column("Draft", justify="right")
    table.add_column("HS", justify="right")
    table.add_column("Spi", justify="right")
    table.add_column("Design")
    table.add_column("Country")

    for b in boats[:limit]:
        table.add_row(
            b["boat_name"],
            b["sail_number"],
            str(b["tcc"]) if b.get("tcc") else "-",
            str(b["non_spi_tcc"]) if b.get("non_spi_tcc") else "-",
            str(b["dlr"]) if b.get("dlr") else "-",
            str(b["lh"]) if b.get("lh") else "-",
            str(b["beam"]) if b.get("beam") else "-",
            str(b["draft"]) if b.get("draft") else "-",
            str(b["headsails"]) if b.get("headsails") else "-",
            str(b["spinnakers"]) if b.get("spinnakers") else "-",
            b.get("design") or "-",
            b.get("country") or "-",
        )

    if len(boats) > limit:
        console.print(f"  [dim]Showing {limit} of {len(boats)} boats[/dim]")
    console.print(table)


@cli.command()
@click.argument("sail_number")
@click.pass_context
def show(ctx, sail_number):
    """Show detailed info for a boat by sail number."""
    engine = ctx.obj["engine"]
    boat = get_boat_detail(engine, sail_number)

    if not boat:
        console.print(f"[red]No boat found with sail number '{sail_number}'[/red]")
        return

    table = Table(title=f"{boat['boat_name']} ({boat['sail_number']})")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    fields = [
        ("Sail Number", "sail_number"),
        ("Cert Number", "cert_number"),
        ("Design", "design"),
        ("Country", "country"),
        ("Year Built", "year_built"),
        ("TCC", "tcc"),
        ("Non-Spi TCC", "non_spi_tcc"),
        ("DLR", "dlr"),
        ("Crew", "crew"),
        ("LH", "lh"),
        ("Beam", "beam"),
        ("Draft", "draft"),
        ("Headsails", "headsails"),
        ("Flying Headsails", "flying_headsails"),
        ("Spinnakers", "spinnakers"),
        ("Single Furling Headsail", "single_furling_headsail"),
        ("Series Date", "series_date"),
        ("Age Date", "age_date"),
        ("Racing Area", "racing_area"),
        ("STIX", "stix"),
        ("AVS", "avs"),
        ("Category", "category"),
        ("Snapshot Date", "snapshot_date"),
    ]

    for label, key in fields:
        val = boat.get(key)
        if val is not None:
            table.add_row(label, str(val))

    console.print(table)


@cli.command()
@click.pass_context
def stats(ctx):
    """Show database statistics."""
    engine = ctx.obj["engine"]
    s = get_stats(engine)
    console.print(f"Boats:           {s['boats']}")
    console.print(f"TCC Snapshots:   {s['snapshots']}")
    console.print(f"Certificates:    {s['certificates']}")
    console.print(f"ORC Certificates: {s.get('orc_certificates', 0)}")
    console.print(f"Race Results:    {s['race_results']}")
    console.print(f"Countries:       {s['countries']}")
    console.print(f"Designs:         {s['designs']}")


# --- Scraper commands ---


@cli.group()
def scrape():
    """Scrape data from external sources."""
    pass


@scrape.command(name="certs")
@click.option("--search", "-s", multiple=True, help="Search term (sail prefix, boat name)")
@click.option("--all-targets", is_flag=True, help="Download all AUS + Sunfast 3300 certs")
@click.option("--exhaustive", is_flag=True, help="2-letter exhaustive search (AA..ZZ, ~17min)")
@click.pass_context
def scrape_certs(ctx, search, all_targets, exhaustive):
    """Download certificate PDFs from ircrating.org."""
    import asyncio

    if exhaustive:
        from irc_data.scrapers.certificate_bulk import (
            download_certificates,
            exhaustive_enumerate,
        )

        console.print("Running exhaustive 2-letter enumeration...")
        certs = asyncio.run(exhaustive_enumerate())
        console.print(f"Found {len(certs)} unique certificates")
        console.print("Downloading new certificates...")
        downloaded = asyncio.run(download_certificates(certs))
        console.print(f"[green]Downloaded {len(downloaded)} certificate PDFs.[/green]")
        return

    if all_targets:
        from irc_data.scrapers.certificate_search import download_all_target_certificates

        console.print("Downloading certificates for all target boats...")
        downloaded = asyncio.run(download_all_target_certificates())
    elif search:
        from irc_data.scrapers.certificate_search import search_and_download_certificates

        console.print(f"Searching for: {', '.join(search)}")
        downloaded = asyncio.run(search_and_download_certificates(list(search)))
    else:
        console.print("[yellow]Specify --search TERM, --all-targets, or --exhaustive[/yellow]")
        return

    console.print(f"[green]Downloaded {len(downloaded)} certificate PDFs.[/green]")


@scrape.command(name="pdf-certs")
@click.option(
    "--max-fetches",
    type=int,
    default=5000,
    help="Maximum total HTTP requests per run (default 5,000)",
)
@click.option(
    "--no-window",
    is_flag=True,
    help="Skip the nightly collection-window check (useful for manual runs)",
)
@click.option(
    "--no-kill-switch",
    is_flag=True,
    help="Skip the per-source kill-switch check",
)
@click.option(
    "--store-path",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the raw object store path (default: data/raw/irc_pdfs)",
)
@click.pass_context
def scrape_pdf_certs(ctx, max_fetches, no_window, no_kill_switch, store_path):
    """Raw-capture IRC certificate PDFs from ircrating.org (DP-00-05).

    Enumerates all known cert numbers from the platform DB, fetches each
    certificate PDF via the public search widget, and stores raw bytes in the
    content-addressed raw object store under data/raw/irc_pdfs/.

    Policy: interim-v0. Polite: 1 req/2s, nightly window 01:00-06:00 UK,
    max 5,000 fetches/night.
    """
    import json as _json

    from irc_data.scrapers.irc_pdf import (
        enumerate_cert_nos_from_db,
        enumerate_cert_nos_from_tcc_dir,
        get_default_store,
        scrape_irc_pdfs,
        RawObjectStore,
    )
    from irc_data.config import TCC_LISTINGS_DIR

    engine = ctx.obj.get("engine")

    # Resolve store
    if store_path:
        store = RawObjectStore(str(store_path))
    else:
        store = get_default_store()

    console.print(f"[bold]IRC PDF Capture (DP-00-05)[/bold]")
    console.print(f"  Store: {store.root}")

    # Enumerate cert numbers
    cert_nos = []
    if engine is not None:
        try:
            cert_nos = enumerate_cert_nos_from_db(engine)
            console.print(f"  Cert numbers from DB: {len(cert_nos):,}")
        except Exception as exc:
            console.print(f"[yellow]DB enumeration failed ({exc}), trying TCC dir[/yellow]")

    if not cert_nos:
        cert_nos = enumerate_cert_nos_from_tcc_dir(TCC_LISTINGS_DIR)
        console.print(f"  Cert numbers from TCC dir: {len(cert_nos):,}")

    if not cert_nos:
        console.print("[red]No cert numbers found. Run 'irc-data scrape tcc' first.[/red]")
        return

    console.print(f"  Max fetches: {max_fetches:,}")
    console.print(f"  Window enforcement: {'off' if no_window else 'on'}")

    ledger = scrape_irc_pdfs(
        cert_nos=cert_nos,
        store=store,
        max_fetches=max_fetches,
        enforce_window=not no_window,
        check_kill_switch=not no_kill_switch and engine is not None,
        db_engine=engine if not no_kill_switch else None,
    )

    console.print(f"\n[bold]Run complete:[/bold]")
    console.print(f"  Status:    {ledger.status}")
    console.print(f"  Found:     {ledger.certs_found:,}")
    console.print(f"  New:       {ledger.certs_new:,}")
    console.print(f"  Unchanged: {ledger.certs_unchanged:,}")
    console.print(f"  Fetches:   {ledger.fetch_count:,}")
    console.print(f"  Errors:    {len(ledger.errors):,}")

    if ledger.errors:
        console.print("\n[yellow]Recent errors:[/yellow]")
        for err in ledger.errors[:5]:
            console.print(f"  cert {err['cert_no']}: {err['message']}")


@scrape.command(name="raw-capture")
@click.option(
    "--source",
    type=click.Choice(
        [
            "sailwave",
            "sailing-news",
            "yachtscoring",
            "manage2sail",
            "dp-00-03",
            "dp-00-04",
            "all",
        ]
    ),
    default="all",
    show_default=True,
    help=(
        "Which raw-capture source to run. 'dp-00-03' = Yacht Scoring + "
        "Manage2Sail; 'dp-00-04' = Sailwave + sailing news; 'all' = both tracks."
    ),
)
@click.option(
    "--max-fetches",
    type=int,
    default=5000,
    show_default=True,
    help="Maximum total HTTP requests per run",
)
@click.option(
    "--no-window",
    is_flag=True,
    help="Skip the nightly collection-window check (manual runs)",
)
@click.option(
    "--no-kill-switch",
    is_flag=True,
    help="Skip the per-source kill-switch / §2 gate check",
)
@click.option(
    "--store-path",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the raw object store root (default: data/raw/<source>)",
)
@click.option(
    "--feed",
    multiple=True,
    help="News feed URL to capture (repeatable; overrides default feed list)",
)
@click.option(
    "--url",
    multiple=True,
    help="Explicit result URL to capture (repeatable; skips discovery)",
)
@click.option(
    "--canary",
    is_flag=True,
    help="DP-00-03 canary mode: cap discovery to a few pages per source "
    "(live canary night, stays well inside rate caps)",
)
@click.option(
    "--max-discovery-pages",
    type=int,
    default=None,
    help="Cap on discovered result pages per source (DP-00-03)",
)
@click.option(
    "--etag-cache-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Persist conditional-request cache (ETag/Last-Modified) for DP-00-03 "
    "sources (default: data/raw/<source>/.etag_cache.json)",
)
@click.pass_context
def scrape_raw_capture(
    ctx, source, max_fetches, no_window, no_kill_switch, store_path, feed, url,
    canary, max_discovery_pages, etag_cache_file,
):
    """Raw archival capture for the DP-00 interim raw-capture tracks.

    DP-00-03: Yacht Scoring + Manage2Sail race-results pages (raw archives,
    no parsing).  DP-00-04: Sailwave result files + approved news feeds.

    Fetch → hash → store into the content-addressed raw object store.
    Envelope: RawArtifactV0 = bytes + SHA-256 + URL + fetch time + policy
    version 'v1.0'.  Idempotent on re-run (304 / hash dedup).

    Policy: v1.0 (interim-v0 politeness rules §3).  Polite: 1 req/2s +
    jitter, nightly window 01:00–06:00, max 5,000 fetches/night.  Sources
    held under §2 of the policy (ClubSpot, Kwindoo) are never fetched.
    """
    from irc_data.scrapers.raw_capture import (
        capture_news_feeds,
        capture_sailwave,
    )
    from irc_data.scrapers.raw_capture import (
        get_default_store as _default_store_04,
    )
    from irc_data.scrapers.raw_capture_ys_m2s import (
        DP_00_03_SOURCES,
        RawObjectStore,
        capture_source,
        load_etag_file,
        save_etag_file,
    )
    from irc_data.scrapers.raw_capture_ys_m2s import (
        get_default_store as _default_store_03,
    )

    engine = ctx.obj.get("engine")

    dp004 = ("sailwave", "sailing-news")
    if source == "all":
        sources = list(DP_00_03_SOURCES) + list(dp004)
    elif source == "dp-00-03":
        sources = list(DP_00_03_SOURCES)
    elif source == "dp-00-04":
        sources = list(dp004)
    else:
        sources = [source]

    console.print("[bold]Raw Capture (DP-00-03 / DP-00-04)[/bold]")
    console.print(f"  Sources: {', '.join(sources)}")
    console.print(f"  Max fetches: {max_fetches:,}")
    console.print(f"  Window enforcement: {'off' if no_window else 'on'}")
    if canary:
        console.print("  [cyan]Canary mode: discovery capped per source[/cyan]")

    overall_status = "ok"
    for slug in sources:
        is_dp003 = slug in DP_00_03_SOURCES
        store = (
            RawObjectStore(str(store_path / slug))
            if store_path
            else (_default_store_03(slug) if is_dp003 else _default_store_04(slug))
        )
        console.print(f"\n[bold]→ {slug}[/bold]  store={store.root}")

        if is_dp003:
            # Load the conditional-request cache (file) for plain-HTTP runs.
            # Default lives alongside the content-addressed store root.
            cache_path = (
                Path(etag_cache_file)
                if etag_cache_file
                else (Path(store.root) / ".etag_cache.json")
            )
            etag_cache = load_etag_file(cache_path)

            ledger = capture_source(
                slug,
                store,
                urls=list(url) if url else None,
                max_fetches=max_fetches,
                max_discovery_pages=max_discovery_pages,
                canary=canary,
                enforce_window=not no_window,
                check_kill_switch=not no_kill_switch,
                db_engine=engine if not no_kill_switch else None,
                etag_cache=etag_cache,
            )
            # Persist the updated conditional-request cache for the next night.
            if cache_path:
                save_etag_file(cache_path, ledger.etag_cache)
        elif slug == "sailwave":
            ledger = capture_sailwave(
                store,
                urls=list(url) if url else None,
                max_fetches=max_fetches,
                enforce_window=not no_window,
                check_kill_switch=not no_kill_switch,
                db_engine=engine if not no_kill_switch else None,
            )
        else:
            ledger = capture_news_feeds(
                store,
                feeds=list(feed) if feed else None,
                max_fetches=max_fetches,
                enforce_window=not no_window,
                check_kill_switch=not no_kill_switch,
                db_engine=engine if not no_kill_switch else None,
            )

        console.print(f"  Status:       {ledger.status}")
        console.print(f"  Attempted:    {ledger.urls_attempted:,}")
        console.print(f"  New:          {ledger.urls_new:,}")
        console.print(f"  Unchanged:    {ledger.urls_unchanged:,}")
        console.print(f"  Not modified: {ledger.urls_not_modified:,}")
        console.print(f"  Skipped:      {ledger.urls_skipped:,}")
        console.print(f"  Fetches:      {ledger.fetch_count:,}")
        console.print(f"  Bytes:        {ledger.bytes_downloaded:,}")
        console.print(f"  Errors:       {len(ledger.errors):,}")

        if ledger.status in ("kill_switch", "window_closed", "robots_error", "error"):
            overall_status = ledger.status

        if ledger.errors:
            console.print("  [yellow]Recent errors:[/yellow]")
            for err in ledger.errors[:5]:
                console.print(f"    {err['url']}: {err['message']}")

    if overall_status != "ok":
        console.print(f"\n[yellow]Overall status: {overall_status}[/yellow]")


@scrape.command(name="wayback")
@click.option("--boat", help="Filter by sail number")
@click.pass_context
def scrape_wayback(ctx, boat):
    """Search Wayback Machine for historical IRC certificate PDFs."""
    import asyncio

    from irc_data.scrapers.wayback import find_and_download_all

    console.print("Searching Wayback Machine for archived IRC PDFs...")
    downloaded = asyncio.run(find_and_download_all())
    console.print(f"[green]Downloaded {len(downloaded)} historical PDFs.[/green]")


@cli.command(name="wayback-tcc")
@click.option("--start-year", type=int, default=2010, show_default=True)
@click.option("--end-year", type=int, default=2025, show_default=True)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Where to write harvested CSVs. Default: "
        "TCC_LISTINGS_DIR/historical."
    ),
)
@click.option(
    "--max-per-pattern",
    type=int,
    default=None,
    help="Cap snapshots per CDX pattern (for smoke testing).",
)
@click.pass_context
def wayback_tcc(ctx, start_year, end_year, out_dir, max_per_pattern):
    """Harvest historical IRC TCC listings from the Wayback Machine."""
    import asyncio

    from irc_data.config import TCC_LISTINGS_DIR
    from irc_data.scrapers.wayback import harvest_tcc_archives

    target = Path(out_dir) if out_dir else (TCC_LISTINGS_DIR / "historical")
    console.print(
        f"Harvesting Wayback TCC snapshots {start_year}-{end_year} -> {target}"
    )
    archives = asyncio.run(
        harvest_tcc_archives(
            start_year=start_year,
            end_year=end_year,
            out_dir=target,
            max_per_pattern=max_per_pattern,
        )
    )
    by_year: dict[int, int] = {}
    for a in archives:
        by_year[a["year"]] = by_year.get(a["year"], 0) + 1
    console.print(
        f"[green]Harvested {len(archives)} CSVs across "
        f"{len(by_year)} year(s).[/green]"
    )
    for yr in sorted(by_year):
        console.print(f"  {yr}: {by_year[yr]}")


@scrape.command(name="results")
@click.option(
    "--source",
    type=click.Choice(["sailsys", "cyca", "sailracehq", "sailwave", "topyacht", "rorc", "isora", "cowesweek", "rhkyc", "sydneyhobart"]),
    default="sailsys",
    help="Race results source",
)
@click.option("--club", default="CYCA", help="Club name for SailSys (CYCA, RPAYC, RSYS, MHYC, MYC, CSC, SPS, NCYC, RQYS, SHCC, ...)")
@click.option("--all-clubs", is_flag=True, help="Scrape all SailSys clubs in sequence")
@click.option("--incremental/--full", default=True, help="Only fetch races newer than last scrape")
@click.option("--max-series", type=int, default=None, help="Limit number of series to scrape")
@click.option("--year", type=int, default=None, help="Year to scrape (RORC, ISORA, Cowes Week, RHKYC, Sydney Hobart)")
@click.option("--store/--no-store", default=True, help="Store results in database")
@click.pass_context
def scrape_results(ctx, source, club, all_clubs, incremental, max_series, year, store):
    """Scrape race results from various sources."""
    import asyncio

    engine = ctx.obj["engine"]
    results = []

    # Helper used by every source-specific upsert below — inject the matching
    # signals into raw_data so rematch passes can find a boat later.
    def _enrich_raw_data(r):
        rd = dict(r.raw_data) if getattr(r, "raw_data", None) else {}
        bname = getattr(r, "boat_name", None)
        sn = getattr(r, "sail_number", None)
        if bname and "boat_name" not in rd:
            rd["boat_name"] = bname
        if sn and "sail_number" not in rd:
            rd["sail_number"] = sn
        return rd

    if source == "sailsys":
        from irc_data.scrapers.sailsys import CLUBS as _CLUB_MAP
        from irc_data.scrapers.sailsys import scrape_club_irc_results
        from irc_data.db.operations import log_ingestion_start, log_ingestion_end

        # Determine which clubs to scrape
        if all_clubs:
            clubs_to_scrape = [pair for pair in _CLUB_MAP.items()]
        else:
            clubs_to_scrape = [(club.upper(), _CLUB_MAP.get(club.upper(), 3))]

        # Heartbeat row for the whole run — so "ran, found nothing" is
        # observable (otherwise import_scraper_results only logs when there
        # were results to import).
        run_log_id = log_ingestion_start(
            engine, "sailsys",
            metadata={"all_clubs": bool(all_clubs), "n_clubs": len(clubs_to_scrape), "incremental": bool(incremental)},
        )

        # Determine incremental cutoff
        since = None
        if incremental:
            with engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT max(created_at)::date - interval '1 day'
                        FROM race_results WHERE source = 'sailsys'
                    """)
                ).first()
                if row and row[0]:
                    since = row[0].date() if hasattr(row[0], 'date') else row[0]
                    console.print(f"[dim]Incremental mode: only races after {since}[/dim]")

        all_results = []
        total_imported = 0
        run_error: str | None = None
        try:
            for club_name, club_id in clubs_to_scrape:
                console.print(f"\nScraping SailSys results for [bold]{club_name}[/bold] (club {club_id})...")
                try:
                    club_results = asyncio.run(scrape_club_irc_results(
                        club_id, max_series=max_series, since=since,
                    ))
                    all_results.extend(club_results)
                    console.print(f"  [green]{len(club_results)} results[/green]")

                    # Store incrementally per club (avoids memory buildup)
                    if store and club_results:
                        from irc_data.scrapers.result_import import import_scraper_results

                        stats = import_scraper_results(
                            engine, club_results, source="sailsys",
                            organizing_club=club_name,
                        )
                        total_imported += stats.get("imported", 0)
                        console.print(f"  Imported: {stats['imported']}, Matched: {stats['matched']}")
                except Exception as e:
                    console.print(f"  [red]Error scraping {club_name}: {e}[/red]")
                    run_error = (run_error + " | " if run_error else "") + f"{club_name}: {e}"

            console.print(f"\n[green]Total: {len(all_results)} results across {len(clubs_to_scrape)} clubs[/green]")
        finally:
            log_ingestion_end(
                engine, run_log_id,
                status="failed" if run_error else "completed",
                records_found=len(all_results),
                records_new=total_imported,
                error_message=(run_error[:1000] if run_error else None),
            )
        return

    elif source == "cyca":
        from irc_data.scrapers.race_results import scrape_cyca_results

        console.print("Scraping CYCA race results...")
        results = asyncio.run(scrape_cyca_results())
    elif source == "rorc":
        from datetime import date as date_type

        from irc_data.scrapers.rorc import RORCSource

        console.print(f"Scraping RORC results{f' for {year}' if year else ''}...")
        rorc = RORCSource()
        since = date_type(year, 1, 1) if year else None
        events = asyncio.run(rorc.discover_events(since=since))
        console.print(f"  Found {len(events)} result files")

        all_results = []
        for event in events:
            event_results = asyncio.run(rorc.scrape_event(event))
            if event_results:
                all_results.extend(event_results)
                console.print(f"  {event.event_name}: {len(event_results)} results")

        if store and all_results:
            from irc_data.scrapers.result_import import _find_boat_by_name
            from irc_data.db.operations import find_boat_by_sail_number, upsert_race_result, log_ingestion_start, log_ingestion_end
            from irc_data.matching.identity import normalize_sail

            log_id = log_ingestion_start(engine, "rorc")
            imported = matched = 0
            for r in all_results:
                boat_id = None
                if r.sail_number:
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
                if not boat_id:
                    boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
                if boat_id:
                    matched += 1
                try:
                    upsert_race_result(
                        engine, boat_id=boat_id,
                        event_name=r.event_name, event_date=r.event_date,
                        race_name=r.race_name, event_series=r.event_series,
                        organizing_club=r.organizing_club, event_type=r.event_type,
                        source="rorc", source_url=r.source_url,
                        rating_type=r.rating_type, rating_value=r.rating_value,
                        place=r.place, fleet_size=r.fleet_size,
                        status=r.status, raw_data=_enrich_raw_data(r),
                    )
                    imported += 1
                except Exception as e:
                    if imported < 3:
                        console.print(f"    [yellow]Error: {e}[/yellow]")

            log_ingestion_end(engine, log_id, records_found=len(all_results), records_new=imported)
            console.print(f"\n[green]RORC: {imported} results stored ({matched} matched to boats)[/green]")
        elif all_results:
            console.print(f"\n[green]Found {len(all_results)} RORC results (not stored, use --store)[/green]")
        return
    elif source == "sydneyhobart":
        from datetime import date as date_type

        from irc_data.scrapers.sydneyhobart import SydneyHobartSource

        console.print(f"Scraping Sydney Hobart results{f' for {year}' if year else ''}...")
        sh = SydneyHobartSource()
        since = date_type(year, 1, 1) if year else None
        events = asyncio.run(sh.discover_events(since=since))

        if year:
            events = [e for e in events if e.metadata and e.metadata.get("year") == year]

        console.print(f"  Found {len(events)} race editions")

        all_results = []
        for event in events:
            event_results = asyncio.run(sh.scrape_event(event))
            if event_results:
                all_results.extend(event_results)
                console.print(f"  {event.event_name}: {len(event_results)} results")

        if store and all_results:
            from irc_data.scrapers.result_import import _find_boat_by_name
            from irc_data.db.operations import find_boat_by_sail_number, upsert_race_result, log_ingestion_start, log_ingestion_end
            from irc_data.matching.identity import normalize_sail

            log_id = log_ingestion_start(engine, "sydneyhobart")
            imported = matched = 0
            for r in all_results:
                boat_id = None
                if r.sail_number:
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
                if not boat_id:
                    boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
                if boat_id:
                    matched += 1
                try:
                    upsert_race_result(
                        engine, boat_id=boat_id,
                        event_name=r.event_name, event_date=r.event_date,
                        race_name=r.race_name, event_series=r.event_series,
                        organizing_club=r.organizing_club, event_type=r.event_type,
                        source="sydneyhobart", source_url=r.source_url,
                        rating_type=r.rating_type, rating_value=r.rating_value,
                        place=r.place, fleet_size=r.fleet_size,
                        status=r.status, raw_data=_enrich_raw_data(r),
                    )
                    imported += 1
                except Exception as e:
                    if imported < 3:
                        console.print(f"    [yellow]Error: {e}[/yellow]")

            log_ingestion_end(engine, log_id, records_found=len(all_results), records_new=imported)
            console.print(f"\n[green]Sydney Hobart: {imported} results stored ({matched} matched to boats)[/green]")
        elif all_results:
            console.print(f"\n[green]Found {len(all_results)} Sydney Hobart results (not stored, use --store)[/green]")
        return
    elif source == "isora":
        from datetime import date as date_type

        from irc_data.scrapers.isora import ISORASource

        console.print(f"Scraping ISORA results{f' for {year}' if year else ''}...")
        isora = ISORASource()
        since = date_type(year, 1, 1) if year else None
        events = asyncio.run(isora.discover_events(since=since))
        console.print(f"  Found {len(events)} result pages")

        all_results = []
        for event in events:
            event_results = asyncio.run(isora.scrape_event(event))
            if event_results:
                all_results.extend(event_results)
                console.print(f"  {event.event_name}: {len(event_results)} results")

        if store and all_results:
            from irc_data.scrapers.result_import import _find_boat_by_name
            from irc_data.db.operations import find_boat_by_sail_number, upsert_race_result, log_ingestion_start, log_ingestion_end
            from irc_data.matching.identity import normalize_sail

            log_id = log_ingestion_start(engine, "isora")
            imported = matched = 0
            for r in all_results:
                boat_id = None
                if r.sail_number:
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
                if not boat_id:
                    boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
                if boat_id:
                    matched += 1
                try:
                    upsert_race_result(
                        engine, boat_id=boat_id,
                        event_name=r.event_name, event_date=r.event_date,
                        race_name=r.race_name, event_series=r.event_series,
                        organizing_club=r.organizing_club, event_type=r.event_type,
                        source="isora", source_url=r.source_url,
                        rating_type=r.rating_type, rating_value=r.rating_value,
                        place=r.place, fleet_size=r.fleet_size,
                        class_name=r.class_name,
                        status=r.status, raw_data=_enrich_raw_data(r),
                    )
                    imported += 1
                except Exception as e:
                    if imported < 3:
                        console.print(f"    [yellow]Error: {e}[/yellow]")

            log_ingestion_end(engine, log_id, records_found=len(all_results), records_new=imported)
            console.print(f"\n[green]ISORA: {imported} results stored ({matched} matched to boats)[/green]")
        elif all_results:
            irc_count = sum(1 for r in all_results if r.rating_value)
            console.print(f"\n[green]Found {len(all_results)} ISORA results ({irc_count} with IRC ratings)[/green]")
        else:
            console.print("[yellow]No ISORA results found.[/yellow]")
        return
    elif source == "cowesweek":
        from datetime import date as date_type

        from irc_data.scrapers.cowesweek import CowesWeekSource

        console.print(f"Scraping Cowes Week results{f' for {year}' if year else ''}...")
        cw = CowesWeekSource()
        since = date_type(year, 1, 1) if year else None
        events = asyncio.run(cw.discover_events(since=since))
        console.print(f"  Found {len(events)} IRC class/year combinations")

        if max_series:
            events = events[:max_series]

        all_results = []
        for event in events:
            event_results = asyncio.run(cw.scrape_event(event))
            if event_results:
                all_results.extend(event_results)
                console.print(f"  {event.event_name}: {len(event_results)} results")

        if store and all_results:
            from irc_data.scrapers.result_import import _find_boat_by_name
            from irc_data.db.operations import find_boat_by_sail_number, upsert_race_result, log_ingestion_start, log_ingestion_end
            from irc_data.matching.identity import normalize_sail

            log_id = log_ingestion_start(engine, "cowesweek")
            imported = matched = 0
            for r in all_results:
                boat_id = None
                if r.sail_number:
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
                if not boat_id:
                    boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
                if boat_id:
                    matched += 1
                try:
                    upsert_race_result(
                        engine, boat_id=boat_id,
                        event_name=r.event_name, event_date=r.event_date,
                        race_name=r.race_name, event_series=r.event_series,
                        organizing_club=r.organizing_club, event_type=r.event_type,
                        source="cowesweek", source_url=r.source_url,
                        rating_type=r.rating_type, rating_value=r.rating_value,
                        place=r.place, fleet_size=r.fleet_size,
                        class_name=r.class_name,
                        status=r.status, raw_data=_enrich_raw_data(r),
                    )
                    imported += 1
                except Exception as e:
                    if imported < 3:
                        console.print(f"    [yellow]Error: {e}[/yellow]")

            log_ingestion_end(engine, log_id, records_found=len(all_results), records_new=imported)
            console.print(f"\n[green]Cowes Week: {imported} results stored ({matched} matched to boats)[/green]")
        elif all_results:
            irc_count = sum(1 for r in all_results if r.rating_value)
            console.print(f"\n[green]Found {len(all_results)} Cowes Week results ({irc_count} with IRC ratings)[/green]")
        else:
            console.print("[yellow]No Cowes Week results found.[/yellow]")
        return
    elif source == "rhkyc":
        from datetime import date as date_type

        from irc_data.scrapers.legacy.rhkyc import RHKYCSource

        console.print(f"Scraping RHKYC results{f' for {year}' if year else ''}...")
        rhkyc = RHKYCSource()
        since = date_type(year, 1, 1) if year else None
        events = asyncio.run(rhkyc.discover_events(since=since))
        console.print(f"  Found {len(events)} result files")

        if max_series:
            events = events[:max_series]

        all_results = []
        for event in events:
            event_results = asyncio.run(rhkyc.scrape_event(event))
            if event_results:
                all_results.extend(event_results)
                console.print(f"  {event.event_name}: {len(event_results)} results")

        if store and all_results:
            from irc_data.scrapers.result_import import _find_boat_by_name
            from irc_data.db.operations import find_boat_by_sail_number, upsert_race_result, log_ingestion_start, log_ingestion_end
            from irc_data.matching.identity import normalize_sail

            log_id = log_ingestion_start(engine, "rhkyc")
            imported = matched = 0
            for r in all_results:
                boat_id = None
                if r.sail_number:
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
                if not boat_id:
                    boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
                if boat_id:
                    matched += 1
                try:
                    upsert_race_result(
                        engine, boat_id=boat_id,
                        event_name=r.event_name, event_date=r.event_date,
                        race_name=r.race_name, event_series=r.event_series,
                        organizing_club=r.organizing_club, event_type=r.event_type,
                        source="rhkyc", source_url=r.source_url,
                        rating_type=r.rating_type, rating_value=r.rating_value,
                        place=r.place, fleet_size=r.fleet_size,
                        class_name=r.class_name,
                        status=r.status, raw_data=_enrich_raw_data(r),
                    )
                    imported += 1
                except Exception as e:
                    if imported < 3:
                        console.print(f"    [yellow]Error: {e}[/yellow]")

            log_ingestion_end(engine, log_id, records_found=len(all_results), records_new=imported)
            console.print(f"\n[green]RHKYC: {imported} results stored ({matched} matched to boats)[/green]")
        elif all_results:
            irc_count = sum(1 for r in all_results if r.rating_value)
            console.print(f"\n[green]Found {len(all_results)} RHKYC results ({irc_count} with IRC ratings)[/green]")
        else:
            console.print("[yellow]No RHKYC results found.[/yellow]")
        return
    elif source == "sailracehq":
        from datetime import date as date_type

        from irc_data.scrapers.sailracehq import SailRaceHQSource

        console.print(f"Scraping SailRaceHQ (RORC 2023+) results{f' for {year}' if year else ''}...")
        srhq = SailRaceHQSource()
        since = date_type(year, 1, 1) if year else date_type(2023, 1, 1)
        events = asyncio.run(srhq.discover_events(since=since))
        console.print(f"  Found {len(events)} events/races")

        if max_series:
            events = events[:max_series]

        all_results = []
        for i, event in enumerate(events):
            console.print(f"\n  [{i + 1}/{len(events)}] {event.event_name}")
            try:
                event_results = asyncio.run(srhq.scrape_event(event))
                if event_results:
                    all_results.extend(event_results)
                    irc_count = sum(1 for r in event_results if r.rating_value)
                    console.print(f"    -> {len(event_results)} results ({irc_count} with IRC TCC)")
            except Exception as e:
                console.print(f"    [red]Error: {e}[/red]")

        if store and all_results:
            from irc_data.scrapers.result_import import _find_boat_by_name
            from irc_data.db.operations import find_boat_by_sail_number, upsert_race_result, log_ingestion_start, log_ingestion_end
            from irc_data.matching.identity import normalize_sail

            log_id = log_ingestion_start(engine, "sailracehq")
            imported = matched = 0
            for r in all_results:
                boat_id = None
                if r.sail_number:
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
                if not boat_id:
                    boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
                if boat_id:
                    matched += 1
                try:
                    upsert_race_result(
                        engine, boat_id=boat_id,
                        event_name=r.event_name, event_date=r.event_date,
                        race_name=r.race_name, event_series=r.event_series,
                        organizing_club=r.organizing_club, event_type=r.event_type,
                        source="sailracehq", source_url=r.source_url,
                        rating_type=r.rating_type, rating_value=r.rating_value,
                        place=r.place, fleet_size=r.fleet_size,
                        status=r.status, raw_data=_enrich_raw_data(r),
                    )
                    imported += 1
                except Exception as e:
                    if imported < 3:
                        console.print(f"    [yellow]Error: {e}[/yellow]")

            log_ingestion_end(engine, log_id, records_found=len(all_results), records_new=imported)
            console.print(f"\n[green]SailRaceHQ: {imported} results stored ({matched} matched to boats)[/green]")
        elif all_results:
            irc_count = sum(1 for r in all_results if r.rating_value)
            console.print(f"\n[green]Found {len(all_results)} SailRaceHQ results ({irc_count} with IRC ratings)[/green]")
        else:
            console.print("[yellow]No SailRaceHQ results found.[/yellow]")
        return
    elif source == "topyacht":
        from irc_data.scrapers.topyacht import TOPYACHT_CLUBS, scrape_all_clubs, scrape_club
        from irc_data.db.operations import log_ingestion_start, log_ingestion_end

        run_log_id = log_ingestion_start(
            engine, "topyacht",
            metadata={"all_clubs": bool(all_clubs or club.upper() not in TOPYACHT_CLUBS), "incremental": bool(incremental)},
        )

        run_error: str | None = None
        all_results = []
        imported = 0
        try:
            # Determine incremental cutoff
            since = None
            if incremental:
                with engine.connect() as conn:
                    row = conn.execute(
                        text("""
                            SELECT max(created_at)::date - interval '1 day'
                            FROM race_results WHERE source = 'topyacht'
                        """)
                    ).first()
                    if row and row[0]:
                        since = row[0].date() if hasattr(row[0], 'date') else row[0]
                        console.print(f"[dim]Incremental mode: only races after {since}[/dim]")

            # Determine which clubs to scrape
            if all_clubs or club.upper() not in TOPYACHT_CLUBS:
                console.print(f"Scraping TopYacht IRC results for all {len(TOPYACHT_CLUBS)} clubs...")
                all_results = asyncio.run(scrape_all_clubs(since=since, max_series=max_series))
            else:
                club_key = club.upper()
                club_name = TOPYACHT_CLUBS[club_key]["club_name"]
                console.print(f"Scraping TopYacht IRC results for [bold]{club_name}[/bold]...")
                all_results = asyncio.run(scrape_club(club_key, since=since, max_series=max_series))

            if store and all_results:
                from irc_data.scrapers.result_import import import_scraper_results

                stats = import_scraper_results(
                    engine, all_results, source="topyacht",
                    organizing_club=TOPYACHT_CLUBS.get(club.upper(), {}).get("club_name"),
                )
                imported = stats.get("imported", 0)
                console.print(f"\n[green]TopYacht: {imported} results stored ({stats['matched']} matched to boats)[/green]")
            elif all_results:
                irc_count = sum(1 for r in all_results if r.tcc_at_race)
                console.print(f"\n[green]Found {len(all_results)} TopYacht results ({irc_count} with IRC TCC)[/green]")
            else:
                console.print("[yellow]No TopYacht results found.[/yellow]")
        except Exception as e:
            run_error = str(e)[:1000]
            console.print(f"[red]TopYacht run failed: {e}[/red]")
        finally:
            log_ingestion_end(
                engine, run_log_id,
                status="failed" if run_error else "completed",
                records_found=len(all_results),
                records_new=imported,
                error_message=run_error,
            )
        return
    elif source == "sailwave":
        from irc_data.scrapers.race_results import scrape_sailwave_results

        console.print("Scraping Sailwave results...")
        results = asyncio.run(scrape_sailwave_results())
    else:
        console.print(f"[yellow]Source '{source}' not yet implemented.[/yellow]")
        return

    console.print(f"[green]Found {len(results)} race results.[/green]")

    # Store results in DB if requested
    if store and results:
        from irc_data.scrapers.result_import import import_scraper_results

        stats = import_scraper_results(engine, results, source=source, organizing_club=club if source == "sailsys" else None)
        console.print(f"  Imported: {stats['imported']}, Matched to boats: {stats['matched']}")
    elif results:
        irc_results = [r for r in results if r.tcc_at_race]
        if irc_results:
            console.print(f"  {len(irc_results)} results have IRC TCC values")


@scrape.command(name="tcc")
@click.option(
    "--no-import",
    is_flag=True,
    help="Only download the CSV; do not import into tcc_snapshots.",
)
@click.pass_context
def scrape_tcc(ctx, no_import: bool):
    """Download latest TCC listing CSV from ircrating.org and import it.

    By default also imports the CSV into tcc_snapshots so a single cron line
    (`irc-data scrape tcc`) keeps IRC rating data fresh end-to-end. Pass
    --no-import to skip the database import.
    """
    import asyncio
    import sys

    from irc_data.db.operations import log_ingestion_start, log_ingestion_end
    from irc_data.scrapers.tcc_listing import download_tcc_listing

    engine = ctx.obj["engine"]

    # Run-log row (DP-00-02): makes the daily TCC run observable in
    # ingestion_log so the health check can report its last-success
    # timestamp. Monitoring only — download/import behaviour is unchanged.
    run_log_id = log_ingestion_start(engine, "irc_tcc")

    run_error: str | None = None
    try:
        console.print("Downloading TCC listing...")
        path = asyncio.run(download_tcc_listing())
        if not path:
            console.print("[red]Download failed.[/red]")
            run_error = "download failed (no CSV saved)"
            log_ingestion_end(
                engine, run_log_id, status="failed", error_message=run_error,
            )
            sys.exit(1)
        console.print(f"[green]Saved: {path}[/green]")

        if no_import:
            log_ingestion_end(engine, run_log_id, status="completed")
            return

        console.print("Importing into tcc_snapshots...")
        ctx.invoke(import_csv, path=path, snapshot_date=str(date.today()))
        log_ingestion_end(engine, run_log_id, status="completed")
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — record the failure before re-raising
        run_error = str(e)[:1000]
        log_ingestion_end(
            engine, run_log_id, status="failed", error_message=run_error,
        )
        raise


@scrape.command(name="historical-certs")
@click.option("--dry-run", is_flag=True, help="Show URLs without downloading")
@click.option("--no-offset", is_flag=True, help="Skip ±10 cert number offset probes")
@click.pass_context
def scrape_historical_certs(ctx, dry_run, no_offset):
    """Download historical certs by probing URLs from CSV data (Strategy 1)."""
    import asyncio

    from irc_data.scrapers.historical_certs import download_all_historical

    console.print("Probing for historical certificates from CSV data...")
    stats = asyncio.run(
        download_all_historical(dry_run=dry_run, include_offset=not no_offset)
    )
    if dry_run:
        console.print(f"[yellow]Dry run: {stats['total']} URLs to try[/yellow]")
    else:
        console.print(
            f"[green]Probed {stats['probed']}, found {stats['found']}, "
            f"downloaded {stats['downloaded']}[/green]"
        )


@cli.command(name="backfill-irc-certs")
@click.option(
    "--tcc-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory of harvested TCC CSV snapshots. "
        "Default: TCC_LISTINGS_DIR/historical."
    ),
)
@click.option(
    "--strategy",
    type=click.Choice(["all", "live", "wayback", "csv"]),
    default="all",
    show_default=True,
)
@click.option("--no-resume", is_flag=True, help="Ignore .irc_backfill_state.json")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap number of certs probed (for testing).",
)
@click.pass_context
def backfill_irc_certs(ctx, tcc_dir, strategy, no_resume, limit):
    """Multi-strategy historical IRC certificate backfill (Plan B orchestrator)."""
    import asyncio

    from irc_data.config import TCC_LISTINGS_DIR
    from irc_data.scrapers.cert_index import build_index_from_tcc_dir
    from irc_data.scrapers.irc_backfill import backfill_from_index

    src = Path(tcc_dir) if tcc_dir else (TCC_LISTINGS_DIR / "historical")
    console.print(f"Building cert-number index from {src}...")
    idx = build_index_from_tcc_dir(src)
    console.print(f"  {len(idx)} unique cert numbers")

    if limit:
        idx = idx[:limit]
        console.print(f"  --limit applied: probing first {len(idx)} entries")

    if not idx:
        console.print("[yellow]Index is empty; run `irc-data wayback-tcc` first.[/yellow]")
        return

    stats = asyncio.run(backfill_from_index(idx, resume=not no_resume))
    console.print(
        f"[green]Found live: {stats['found_live']}, "
        f"wayback: {stats['found_wayback']}, "
        f"missing: {stats['not_found']}[/green]"
    )


@cli.command(name="history-reconstruction")
@click.option("--dry-run", is_flag=True, help="report only; no DB writes / downloads")
@click.option("--skip-harvest", is_flag=True, help="skip Phase A (wayback harvest)")
@click.option("--skip-import", is_flag=True, help="skip Phase B (tcc_snapshots import)")
@click.option("--skip-backfill", is_flag=True, help="skip Phase C (cert PDF backfill)")
@click.option("--backfill-limit", type=int, default=None, help="cap Phase C probes")
@click.option("--progress-every", type=int, default=100, show_default=True,
              help="admin_metrics progress cadence during Phase C")
@click.option("--no-resume", is_flag=True, help="ignore .irc_backfill_state.json")
@click.option("--start-year", type=int, default=2010, show_default=True)
@click.option("--end-year", type=int, default=2025, show_default=True)
@click.option("--max-per-pattern", type=int, default=None,
              help="smoke-test cap per CDX pattern")
@click.option("--tcc-dir", default=None, help="override harvested-CSV dir")
@click.pass_context
def history_reconstruction(ctx, dry_run, skip_harvest, skip_import, skip_backfill,
                           backfill_limit, progress_every, no_resume,
                           start_year, end_year, max_per_pattern, tcc_dir):
    """OPS-02-12 — IRC history reconstruction at scale.

    Orchestrates the Wayback TCC harvest + historical TCC import +
    prioritized irc_backfill, recording progress in admin_metrics and the
    acceptance KPI (>=60% of 24-month racers with >=3y TCC history)
    before/after the run.
    """
    import argparse

    from scripts.ops_02_12_history_reconstruction import _print, run

    ns = argparse.Namespace(
        dry_run=dry_run,
        skip_harvest=skip_harvest,
        skip_import=skip_import,
        skip_backfill=skip_backfill,
        backfill_limit=backfill_limit,
        progress_every=progress_every,
        no_resume=no_resume,
        start_year=start_year,
        end_year=end_year,
        max_per_pattern=max_per_pattern,
        tcc_dir=tcc_dir,
    )
    report = run(ctx.obj["engine"], ns)
    _print(report)
    kpi = report["kpi_after"]
    colour = "green" if kpi["meets_acceptance"] else "yellow"
    console.print(
        f"[{colour}]Acceptance KPI: {kpi['with_3y_span']}/{kpi['racers']} "
        f"24-month racers have >=3y TCC history "
        f"({100 * kpi['pct_span']:.1f}%; threshold 60%).[/{colour}]"
    )


@scrape.command(name="cert-probe")
@click.option("--design", "-d", default="Sunfast 3300", help="Boat design to probe")
@click.option("--range", "scan_range", type=int, default=5000, help="How far back to scan")
@click.option("--concurrent", type=int, default=5, help="Max concurrent requests")
@click.option("--dry-run", is_flag=True, help="Show plan without executing")
@click.option("--all", "all_boats", is_flag=True, help="Scan ALL boats in database")
@click.option("--start-cert", type=int, default=None, help="Only boats with cert >= this")
@click.option("--end-cert", type=int, default=None, help="Only boats with cert <= this")
@click.pass_context
def scrape_cert_probe(ctx, design, scan_range, concurrent, dry_run, all_boats, start_cert, end_cert):
    """Smart backward cert scanning for boats (Strategy 3).

    By default, scans a specific design. Use --all to scan ALL boats.
    Use --start-cert/--end-cert to process boats in parallel batches.
    """
    import asyncio

    from irc_data.scrapers.cert_probe import probe_design_boats

    if all_boats:
        label = "all boats"
    else:
        label = f"{design} boats"

    range_str = ""
    if start_cert or end_cert:
        range_str = f" (cert range {start_cert or 1}-{end_cert or 'max'})"

    console.print(f"Cert probe for {label} (scan range: {scan_range}){range_str}...")
    stats = asyncio.run(
        probe_design_boats(
            design=design,
            scan_range=scan_range,
            max_concurrent=concurrent,
            dry_run=dry_run,
            all_boats=all_boats,
            start_cert=start_cert,
            end_cert=end_cert,
        )
    )
    if dry_run:
        console.print(f"[yellow]Dry run: {stats['boats']} boats to scan[/yellow]")
    else:
        console.print(
            f"[green]{stats['found']} historical certs found, "
            f"{stats['downloaded']} downloaded[/green]"
        )


@scrape.command(name="orc")
@click.option("--country", "-c", multiple=True, help="Specific country code(s) (e.g. AUS, GBR)")
@click.option("--snapshot-date", default=None, help="Override snapshot date (YYYY-MM-DD)")
@click.option("--no-archive", is_flag=True, help="Don't save raw XML files")
@click.pass_context
def scrape_orc(ctx, country, snapshot_date, no_archive):
    """Download all ORC certificates from data.orc.org."""
    import asyncio
    from datetime import date as date_type

    from irc_data.scrapers.orc import scrape_all_countries

    snap = date_type.fromisoformat(snapshot_date) if snapshot_date else None
    countries = [c for c in country] if country else None

    if countries:
        console.print(f"Scraping ORC certificates for: {', '.join(countries)}")
    else:
        console.print("Scraping ORC certificates for ALL countries...")

    stats = asyncio.run(scrape_all_countries(
        snapshot_date=snap,
        archive_raw=not no_archive,
        countries=countries,
    ))

    console.print(f"\n[green]ORC scrape complete:[/green]")
    console.print(f"  Countries: {stats['countries']}")
    console.print(f"  Certificates found: {stats['total_found']}")
    console.print(f"  New records: {stats['total_new']}")
    console.print(f"  Snapshot date: {stats['snapshot_date']}")
    if stats['errors']:
        console.print(f"  [yellow]Errors: {len(stats['errors'])}[/yellow]")
        for err in stats['errors']:
            console.print(f"    {err}")


@scrape.command(name="orc-detail")
@click.option("--limit", "-l", type=int, default=None, help="Max certs to fetch (rate-limit-friendly).")
@click.option(
    "--backlog",
    is_flag=True,
    help=(
        "Backlog mode: process only certs missing GPH/CDL/allowances. "
        "When omitted, the command behaves identically (the underlying "
        "implementation already filters to NULL-GPH rows on the latest "
        "snapshot); the flag exists to make cron intent explicit and to "
        "default --limit to 500 for nightly runs."
    ),
)
@click.pass_context
def scrape_orc_detail(ctx, limit, backlog):
    """Backfill ORC certificate detail data (GPH, CDL, polars) from DownBoatRMS API."""
    import asyncio

    from irc_data.scrapers.orc import backfill_orc_details

    # In --backlog mode, default to 500 certs/run unless the operator overrides.
    if backlog and limit is None:
        limit = 500

    console.print("Backfilling ORC certificate details (GPH, CDL, dimensions, polars)...")
    if backlog:
        console.print(f"  Mode: backlog (limit={limit})")
    stats = asyncio.run(backfill_orc_details(limit=limit))

    console.print(f"\n[green]ORC detail backfill complete:[/green]")
    console.print(f"  Certs missing data: {stats['total_missing']}")
    console.print(f"  Successfully fetched: {stats['fetched']}")
    console.print(f"  Errors: {stats['errors']}")


@scrape.command(name="snapshot")
@click.pass_context
def scrape_snapshot(ctx):
    """Weekly snapshot: download TCC CSV, enumerate certs, detect changes (Strategy 5)."""
    import asyncio
    import shutil
    from datetime import date

    from irc_data.config import CERTIFICATES_DIR, TCC_LISTINGS_DIR
    from irc_data.scrapers.certificate_bulk import (
        download_certificates,
        enumerate_all_certificates,
    )
    from irc_data.scrapers.historical_certs import build_cert_url, download_cert
    from irc_data.scrapers.base import get_http_client

    today = date.today().isoformat()

    # Step 1: Download fresh TCC listing
    console.print("[bold]Step 1:[/bold] Downloading TCC listing...")
    try:
        from irc_data.scrapers.tcc_listing import download_tcc_listing

        path = asyncio.run(download_tcc_listing())
        if path:
            console.print(f"  Saved: {path}")
    except Exception as e:
        console.print(f"  [yellow]TCC download failed: {e}[/yellow]")

    # Step 2: Enumerate all current certs
    console.print("[bold]Step 2:[/bold] Enumerating current certificates...")
    current_certs = asyncio.run(enumerate_all_certificates())
    console.print(f"  Found {len(current_certs)} current certificates")

    # Step 3: Compare with what we have on disk
    existing = set()
    if CERTIFICATES_DIR.exists():
        for pdf in CERTIFICATES_DIR.glob("*.pdf"):
            parts = pdf.stem.split("_", 1)
            if parts[0].isdigit():
                existing.add(parts[0])

    current_cert_nos = {c["cert_number"] for c in current_certs if c.get("cert_number")}
    disappeared = existing - current_cert_nos
    new_certs = [c for c in current_certs if c.get("cert_number") not in existing]

    if disappeared:
        console.print(f"  [yellow]{len(disappeared)} certs disappeared from search[/yellow]")

    if new_certs:
        console.print(f"  [green]{len(new_certs)} new certificates to download[/green]")
        downloaded = asyncio.run(download_certificates(new_certs))
        console.print(f"  Downloaded {len(downloaded)} new PDFs")
    else:
        console.print("  No new certificates")

    # Step 4: Try to grab disappeared certs by direct URL (may still be on disk)
    if disappeared:
        console.print(f"[bold]Step 3:[/bold] Trying to grab {len(disappeared)} disappeared certs...")
        grabbed = 0

        async def grab_disappeared():
            nonlocal grabbed
            async with get_http_client() as client:
                for cert_no in disappeared:
                    # Find the cert info from our existing files
                    for pdf in CERTIFICATES_DIR.glob(f"{cert_no}_*.pdf"):
                        parts = pdf.stem.split("_", 2)
                        if len(parts) == 3:
                            url = build_cert_url(parts[0], parts[1], parts[2])
                            from irc_data.scrapers.historical_certs import download_limiter
                            await download_limiter.wait()
                            result = await download_cert(client, url, CERTIFICATES_DIR)
                            if result:
                                grabbed += 1
                        break

        asyncio.run(grab_disappeared())
        console.print(f"  Grabbed {grabbed} disappeared certificates")

    console.print(f"[green]Snapshot complete for {today}[/green]")


@cli.command(name="ingest-event")
@click.option("--url", default=None,
              help="Race results page URL. Optional when --source + --year "
                   "uniquely identifies the event (cowesweek, sydneyhobart).")
@click.option(
    "--source",
    type=click.Choice(["cowesweek", "sydneyhobart", "rhkyc", "isora",
                       "sailracehq", "sailwave", "yachtscoring", "rpayc",
                       "firecrawl"]),
    default="firecrawl",
    help="Value written to race_results.source. Use the legacy source name "
         "when cutting over; 'firecrawl' for parallel-run mode.",
)
@click.option("--year", type=int, default=None,
              help="Archive year for annual events (cowesweek, sydneyhobart). "
                   "If --url is omitted, the canonical URL for the year is "
                   "derived from --source.")
@click.option("--dry-run", is_flag=True,
              help="Scrape + extract + print results, do NOT write to DB")
@click.pass_context
def ingest_event(ctx, url, source, year, dry_run):
    """Scrape one event URL via Firecrawl, extract via Claude, import to race_results.

    This is the crawler-path replacement for `scrape results --source X`
    for sources without a structured API. The same pipeline handles every
    target site — Firecrawl normalises HTML/PDF to markdown and Claude
    pulls a typed RaceResult[] out via tool_use.

    Annual events accept --year and derive the URL:
      irc-data ingest-event --source cowesweek --year 2024
      irc-data ingest-event --source sydneyhobart --year 2024
    """
    from decimal import Decimal

    from irc_data.discovery.firecrawl_client import scrape_url
    from irc_data.discovery.extractor import extract_results
    from irc_data.parsers.schemas import RaceResult
    from irc_data.scrapers.result_import import import_scraper_results

    # Resolve --url from --source + --year for annual events whose archive
    # URL pattern is stable. Sydney-Hobart's pattern on cyca.com.au points to
    # the entries-closed marketing page rather than the results, and the real
    # results live on bwps.cycaracing.com (year-specific URLs vary), so we
    # require --url explicitly for that source.
    if not url and year:
        if source == "cowesweek":
            url = f"https://www.cowesweek.co.uk/results/{year}"
        elif source == "sydneyhobart":
            console.print(
                "[yellow]--year alone isn't enough for sydneyhobart — the "
                "results URL pattern on cyca.com.au is unreliable. Pass "
                "--url pointing at the BWPS race page on "
                "bwps.cycaracing.com (e.g. .../results or "
                ".../?race=N).[/yellow]"
            )
            raise SystemExit(2)
    if not url:
        console.print(
            "[red]--url is required (or pass --source cowesweek with --year)[/red]"
        )
        raise SystemExit(2)

    engine = ctx.obj["engine"]
    console.print(f"[cyan]Scraping[/cyan] {url}")

    scraped = scrape_url(url, caller="cli.ingest-event")
    if not scraped.markdown.strip():
        console.print("[red]Firecrawl returned no content — aborting[/red]")
        raise SystemExit(2)
    console.print(f"  scraped {len(scraped.markdown):,} chars  title={scraped.title!r}")

    extraction = extract_results(url, scraped.markdown)
    if extraction.get("_error"):
        console.print(f"[red]Extractor failed: {extraction['_error']}[/red]")
        raise SystemExit(2)

    rows = extraction.get("results", [])
    console.print(
        f"  event={extraction.get('event_name')!r}  "
        f"class={extraction.get('class_name')!r}  "
        f"confidence={extraction.get('confidence')}  "
        f"rows={len(rows)}"
    )
    if not rows:
        console.print("[yellow]Extractor returned 0 rows — nothing to import[/yellow]")
        return

    event_name = extraction.get("event_name") or scraped.title or "Unknown Event"
    event_date = None
    if extraction.get("event_date"):
        from datetime import datetime as _dt
        try:
            event_date = _dt.fromisoformat(extraction["event_date"]).date()
        except Exception:
            event_date = None
    race_name = extraction.get("race_name")
    class_name = extraction.get("class_name")

    def _dec(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    race_results: list[RaceResult] = []
    for r in rows:
        rating = _dec(r.get("rating_value"))
        rd = {
            "boat_name": r.get("boat_name"),
            "sail_number": r.get("sail_number"),
            "fleet_size": len(rows),
            "division": class_name,
            "race_name": race_name,
            "rating_type": "irc_tcc" if rating else None,
            "rating_value": float(rating) if rating else None,
            "status": r.get("status"),
            "elapsed_time": r.get("elapsed_time"),
            "corrected_time": r.get("corrected_time"),
            "confidence": extraction.get("confidence"),
        }
        race_results.append(RaceResult(
            event_name=event_name,
            event_date=event_date,
            source_url=url,
            tcc_at_race=rating,
            place=r.get("place"),
            division=class_name,
            elapsed_time=r.get("elapsed_time"),
            corrected_time=r.get("corrected_time"),
            raw_data=rd,
        ))

    # Confidence gate. Extractor returns its own self-assessed confidence;
    # anything below CONFIDENCE_FLOOR is treated as untrusted and logged to
    # ingest_events as 'quarantined' rather than written to race_results.
    from irc_data.discovery.extractor import CONFIDENCE_FLOOR
    from irc_data.db.ingest_log import log_event

    conf = float(extraction.get("confidence") or 0.0)
    if conf < CONFIDENCE_FLOOR:
        console.print(
            f"[yellow]Quarantined[/yellow]: extractor confidence "
            f"{conf:.2f} < floor {CONFIDENCE_FLOOR:.2f}. "
            f"{len(race_results)} rows NOT imported."
        )
        if not dry_run:
            log_event(
                engine,
                source=source,
                event_type="extract",
                status="quarantined",
                reference=url,
                reason=(
                    f"confidence={conf:.2f} below floor "
                    f"{CONFIDENCE_FLOOR:.2f}; {len(race_results)} rows skipped"
                ),
                meta={
                    "event_name": event_name,
                    "class_name": class_name,
                    "row_count": len(race_results),
                    "confidence": conf,
                },
            )
        for r in race_results[:5]:
            console.print(
                f"  (quarantined) {r.place}  "
                f"{r.raw_data.get('boat_name')!r}  TCC={r.tcc_at_race}"
            )
        if len(race_results) > 5:
            console.print(f"  ... and {len(race_results) - 5} more")
        return

    # Recall gate. Compare named-boat count against the legacy baseline for this
    # URL. Only applied when ≥5 named legacy boats exist (i.e. we have a real
    # baseline to compare against). Count-based estimate — not full name-matching.
    from irc_data.discovery.extractor import RECALL_FLOOR
    from irc_data.scrapers.result_import import named_legacy_count

    baseline = named_legacy_count(engine, source, url)
    if baseline >= 5:
        fc_named = sum(1 for r in race_results if r.raw_data.get("boat_name"))
        recall_est = fc_named / baseline
        if recall_est < RECALL_FLOOR:
            console.print(
                f"[yellow]Recall gate[/yellow]: estimated recall "
                f"{recall_est:.2f} ({fc_named}/{baseline}) < floor "
                f"{RECALL_FLOOR:.2f}. {len(race_results)} rows NOT imported."
            )
            if not dry_run:
                log_event(
                    engine,
                    source=source,
                    event_type="extract",
                    status="quarantined",
                    reference=url,
                    reason=(
                        f"recall_est={recall_est:.2f} below floor "
                        f"{RECALL_FLOOR:.2f}; legacy_named={baseline} "
                        f"firecrawl_named={fc_named}"
                    ),
                    meta={
                        "fc_named": fc_named,
                        "legacy_named": baseline,
                        "row_count": len(race_results),
                        "confidence": conf,
                    },
                )
            return

    if dry_run:
        console.print("[yellow]--dry-run: not writing to DB[/yellow]")
        for r in race_results[:5]:
            console.print(f"  {r.place}  {r.raw_data.get('boat_name')!r}  TCC={r.tcc_at_race}")
        if len(race_results) > 5:
            console.print(f"  ... and {len(race_results) - 5} more")
        return

    stats = import_scraper_results(
        engine, race_results, source=source, transport="firecrawl"
    )
    console.print(
        f"[green]Imported[/green] {stats['imported']}/{stats['total']} rows  "
        f"({stats['matched']} matched to boats, {stats['errors']} errors)"
    )


@cli.command(name="firecrawl-diff")
@click.option(
    "--source",
    type=click.Choice(["cowesweek", "sydneyhobart", "rhkyc", "isora",
                       "sailracehq", "sailwave"]),
    required=True,
    help="Legacy source to compare against",
)
@click.option("--limit", type=int, default=5,
              help="Number of source_urls to replay (newest first)")
@click.option("--days", type=int, default=365,
              help="Only consider source_urls with event_date in the last N days")
@click.option("--url", "explicit_url", default=None,
              help="Spot-check a single URL instead of sampling from the DB.")
@click.pass_context
def firecrawl_diff(ctx, source, limit, days, explicit_url):
    """Replay recent event URLs through the Firecrawl extractor and log
    a row-level comparison against the legacy rows in race_results.

    Writes one row per event to firecrawl_diffs. Surfaced on
    /justin/firecrawl so we can watch the gap shrink (or not) across the
    parallel-run window before retiring the bespoke scraper.
    """
    import json as _json
    import re

    from irc_data.discovery.firecrawl_client import scrape_url
    from irc_data.discovery.extractor import extract_results

    engine = ctx.obj["engine"]

    def _norm(s):
        if not s:
            return ""
        s = s.upper()
        s = re.sub(r"\s*\((DH|TH|DOUBLE.?HANDED|TWO.?HANDED)\)\s*", "", s)
        s = re.sub(r"[^A-Z0-9]+", "", s)
        return s

    def _name_match(a, b):
        if not a or not b:
            return False
        if a == b:
            return True
        return (a in b or b in a) and min(len(a), len(b)) >= 3

    if explicit_url:
        from types import SimpleNamespace
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT MIN(event_name) AS event_name, MIN(event_date) AS event_date
                FROM race_results
                WHERE source = :source AND source_url = :url
            """), {"source": source, "url": explicit_url}).fetchone()
        urls = [SimpleNamespace(
            source_url=explicit_url,
            event_name=row.event_name if row else None,
            event_date=row.event_date if row else None,
        )]
    else:
        with engine.connect() as conn:
            urls = conn.execute(text("""
                SELECT source_url,
                       MIN(event_name) AS event_name,
                       MIN(event_date) AS event_date,
                       COUNT(*) AS rows
                FROM race_results
                WHERE source = :source
                  AND source_url IS NOT NULL
                  AND (event_date IS NULL OR event_date > now() - make_interval(days => :days))
                GROUP BY source_url
                HAVING COUNT(*) >= 5
                ORDER BY MAX(event_date) DESC NULLS LAST, MIN(id) DESC
                LIMIT :limit
            """), {"source": source, "days": days, "limit": limit}).fetchall()

    if not urls:
        console.print(f"[yellow]No source_urls found for source={source!r}[/yellow]")
        return

    console.print(f"[cyan]firecrawl-diff[/cyan] {source}: comparing {len(urls)} URL(s)")

    for u in urls:
        url = u.source_url
        with engine.connect() as conn:
            db_rows = conn.execute(text("""
                SELECT raw_data->>'boat_name' AS boat_name
                FROM race_results
                WHERE source_url = :url
            """), {"url": url}).fetchall()
        db_names = {_norm(r.boat_name) for r in db_rows if r.boat_name}
        db_names.discard("")

        try:
            scraped = scrape_url(url, caller="cli.firecrawl-diff")
        except Exception as e:
            console.print(f"  [red]scrape failed:[/red] {url[:90]}  ({e})")
            continue

        extraction = extract_results(url, scraped.markdown)
        new_names_raw = [r.get("boat_name") for r in extraction.get("results", [])]
        new_names = {_norm(n) for n in new_names_raw if n}
        new_names.discard("")

        # Tolerant matching (containment)
        matched: set[str] = set()
        used: set[str] = set()
        for n in db_names:
            if n in new_names:
                matched.add(n)
                used.add(n)
                continue
            for m in new_names:
                if m in used:
                    continue
                if _name_match(n, m):
                    matched.add(n)
                    used.add(m)
                    break

        missing = sorted(db_names - matched - used)[:25]
        extra = sorted(new_names - used)[:25]
        rate = len(matched) / len(db_names) if db_names else 0.0
        confidence = extraction.get("confidence") or 0.0

        # Many legacy scrapers emit placeholder rows with no boat_name
        # (e.g. ISORA series pages where most cells are "(0.0 DNC)"). The
        # recall denominator (db_names) is the *named* legacy set; the raw
        # row count is informational. We persist both so the dashboard can
        # disambiguate honest under-extraction from legacy-side hollowness.
        legacy_total = len(db_rows)
        legacy_named = len(db_names)
        hollow_pct = (
            (legacy_total - legacy_named) * 100 // legacy_total
            if legacy_total else 0
        )

        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO firecrawl_diffs
                  (source, source_url, event_name, event_date, legacy_rows,
                   firecrawl_rows, matched, match_rate, confidence,
                   missing_names, extra_names, notes)
                VALUES
                  (:source, :url, :event_name, :event_date, :legacy, :fc,
                   :matched, :rate, :conf, CAST(:miss AS jsonb),
                   CAST(:extra AS jsonb), :notes)
            """), {
                "source": source,
                "url": url,
                "event_name": extraction.get("event_name") or u.event_name,
                "event_date": u.event_date,
                "legacy": legacy_named,  # recall denominator, not raw count
                "fc": len(new_names_raw),
                "matched": len(matched),
                "rate": round(rate, 3),
                "conf": round(confidence, 3),
                "miss": _json.dumps(missing),
                "extra": _json.dumps(extra),
                "notes": (
                    extraction.get("_error")
                    or (
                        f"legacy_total={legacy_total} of which "
                        f"{hollow_pct}% were hollow (no boat_name)"
                        if hollow_pct >= 25 else None
                    )
                ),
            })

        verdict = "[green]GREEN[/green]" if rate >= 0.95 else "[yellow]AMBER[/yellow]" if rate >= 0.85 else "[red]RED[/red]"
        hollow_tag = f" (legacy raw={legacy_total} hollow={hollow_pct}%)" if hollow_pct >= 25 else ""
        console.print(
            f"  {verdict} {rate*100:5.1f}%  "
            f"legacy_named={legacy_named:>3} fc={len(new_names_raw):>3} "
            f"matched={len(matched):>3}{hollow_tag}  {url[:80]}"
        )

    console.print(f"[cyan]Done[/cyan] — view results at /justin/firecrawl (Diffs panel)")


@cli.command(name="parse-certs")
@click.option("--dir", "cert_dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--include-historical",
    is_flag=True,
    help=(
        "Also sweep HISTORICAL_CERTS_DIR (PDFs harvested by "
        "`backfill-irc-certs`)."
    ),
)
@click.pass_context
def parse_certs(ctx, cert_dir, include_historical):
    """Parse downloaded certificate PDFs and insert into database."""
    from irc_data.config import CERTIFICATES_DIR, HISTORICAL_CERTS_DIR
    from irc_data.db.operations import (
        find_boat_by_cert_number,
        find_boat_by_sail_number,
        upsert_certificate,
    )
    from irc_data.parsers.certificate_pdf import (
        parse_all_certificates,
        parse_filename_info,
    )

    engine = ctx.obj["engine"]
    dirs: list[Path] = [Path(cert_dir) if cert_dir else CERTIFICATES_DIR]
    if include_historical and HISTORICAL_CERTS_DIR not in dirs:
        dirs.append(HISTORICAL_CERTS_DIR)

    results: list = []
    for d in dirs:
        console.print(f"Parsing PDFs in {d}...")
        if not d.exists():
            console.print(f"  [yellow]{d} does not exist; skipping[/yellow]")
            continue
        results.extend(parse_all_certificates(d))
    console.print(f"Parsed {len(results)} certificates")

    inserted = 0
    matched = 0
    for cert in results:
        # Use cert_number from PDF, or fall back to filename
        fn_info = parse_filename_info(Path(cert.pdf_path).name)
        cert_number = cert.cert_number or fn_info.get("cert_number")

        if not cert_number:
            continue

        # Try to match to a boat in the database
        boat_id = find_boat_by_cert_number(engine, cert_number)
        if not boat_id:
            sail_no = fn_info.get("sail_number")
            if sail_no:
                boat_id = find_boat_by_sail_number(engine, sail_no)

        if boat_id:
            matched += 1

        cert_dict = {
            "cert_number": cert_number,
            "issue_date": cert.issue_date,
            "source": cert.source,
            "pdf_path": cert.pdf_path,
            "lh": cert.lh,
            "beam": cert.beam,
            "draft": cert.draft,
            "displacement": cert.displacement_kg,
            "bo": cert.bo,
            "so": cert.so,
            "p": cert.p,
            "e": cert.e,
            "j": cert.j,
            "fl": cert.fl,
            "stl": cert.stl,
            "spl": cert.spl,
            "rig_type": cert.rig_type,
            "mast_material": cert.mast_material,
            "spreaders": cert.spreaders,
            "muw": cert.muw,
            "mtw": cert.mtw,
            "mhw": cert.mhw,
            "hlu": cert.hlu,
            "hlp": cert.hlp,
            "hhw": cert.hhw,
            "htw": cert.htw,
            "huw": cert.huw,
            "sym_slu": cert.sym_slu,
            "sym_sle": cert.sym_sle,
            "sym_sf": cert.sym_sf,
            "sym_shw": cert.sym_shw,
            "asym_slu": cert.asym_slu,
            "asym_sle": cert.asym_sle,
            "asym_sf": cert.asym_sf,
            "asym_shw": cert.asym_shw,
            "water_ballast": cert.water_ballast,
            "stix": cert.stix,
            "avs": cert.avs,
            "design_category": cert.design_category,
            # Extended measurements
            "lwp": cert.lwp,
            "dlr": cert.dlr,
            "x": cert.x,
            "y": cert.y,
            "internal_ballast": cert.internal_ballast,
            "hsa": cert.hsa,
            "headsails_max": cert.headsails_max,
            "flying_headsails_max": cert.flying_headsails_max,
            "fsa": cert.fsa,
            "flu": cert.flu,
            "flp": cert.flp,
            "fuw": cert.fuw,
            "ftw": cert.ftw,
            "fhw": cert.fhw,
            "fsfl": cert.fsfl,
            "fshw": cert.fshw,
            "spa": cert.spa,
            "spinnakers_max": cert.spinnakers_max,
            "stl_fh_max": cert.stl_fh_max,
            "aft_rigging": cert.aft_rigging,
            "raw_data": cert.raw_data,
        }
        upsert_certificate(engine, boat_id, cert_dict)
        inserted += 1

    console.print(
        f"[green]Inserted {inserted} certificates ({matched} matched to boats).[/green]"
    )


# --- Analysis commands ---


@cli.group()
def analyze():
    """Run analysis queries."""
    pass


@analyze.command(name="sunfast")
@click.pass_context
def analyze_sunfast(ctx):
    """Analyze Sunfast 3300 fleet — TCC drivers, sail configs, sensitivities."""
    from irc_data.analysis.sunfast import (
        get_sunfast_certificates,
        sail_config_analysis,
        sensitivity_analysis,
    )

    engine = ctx.obj["engine"]

    # Sail config analysis
    console.print("\n[bold]Sail Configuration vs TCC[/bold]")
    configs = sail_config_analysis(engine)
    config_table = Table()
    config_table.add_column("Headsails", justify="right")
    config_table.add_column("Spinnakers", justify="right")
    config_table.add_column("Count", justify="right")
    config_table.add_column("TCC Range")
    config_table.add_column("Avg TCC", justify="right")
    config_table.add_column("Boats")
    for c in configs:
        config_table.add_row(
            str(c["headsails"]),
            str(c["spinnakers"]),
            str(c["count"]),
            f"{c['min_tcc']}-{c['max_tcc']}",
            f"{c['avg_tcc']:.4f}",
            ", ".join(c["boats"][:4]),
        )
    console.print(config_table)

    # Sensitivity analysis
    sens = sensitivity_analysis(engine)
    if sens:
        console.print(f"\n[bold]Measurement Sensitivity (n={sens['n_boats']} boats)[/bold]")
        console.print(f"TCC Range: {sens['tcc_range'][0]:.3f} - {sens['tcc_range'][1]:.3f}")

        sens_table = Table()
        sens_table.add_column("Measurement")
        sens_table.add_column("Range")
        sens_table.add_column("Corr w/ TCC", justify="right")
        sens_table.add_column("Impact", justify="right")

        for field, corr in sorted(
            sens["correlations"].items(), key=lambda x: abs(x[1]), reverse=True
        ):
            rng = sens["ranges"].get(field, (0, 0))
            direction = "+" if corr > 0 else "-"
            bar = "#" * int(abs(corr) * 10)
            sens_table.add_row(
                field,
                f"{rng[0]:.2f} - {rng[1]:.2f}",
                f"{corr:+.3f}",
                f"{direction} {bar}",
            )
        console.print(sens_table)

    # Certificate detail table
    certs = get_sunfast_certificates(engine)
    if certs:
        console.print(f"\n[bold]Sunfast 3300 Certificate Details ({len(certs)} boats)[/bold]")
        detail_table = Table()
        detail_table.add_column("Boat", style="bold")
        detail_table.add_column("Sail No")
        detail_table.add_column("TCC", justify="right")
        detail_table.add_column("Weight", justify="right")
        detail_table.add_column("P", justify="right")
        detail_table.add_column("E", justify="right")
        detail_table.add_column("STL", justify="right")
        detail_table.add_column("MUW", justify="right")
        detail_table.add_column("MHW", justify="right")
        detail_table.add_column("HLU", justify="right")
        detail_table.add_column("SLU", justify="right")
        detail_table.add_column("WB", justify="right")
        for c in certs:
            detail_table.add_row(
                c["boat_name"],
                c["sail_number"],
                str(c["tcc"]),
                str(c.get("weight") or "-"),
                str(c.get("p") or "-"),
                str(c.get("e") or "-"),
                str(c.get("stl") or "-"),
                str(c.get("muw") or "-"),
                str(c.get("mhw") or "-"),
                str(c.get("hlu") or "-"),
                str(c.get("sym_slu") or "-"),
                f"{c['water_ballast']}L" if c.get("water_ballast") else "-",
            )
        console.print(detail_table)


@analyze.command(name="fleet")
@click.option("--design", "-d", help="Filter by design")
@click.option("--country", "-c", help="Filter by country")
@click.pass_context
def analyze_fleet(ctx, design, country):
    """Show fleet statistics."""
    from irc_data.analysis.compare import fleet_stats

    engine = ctx.obj["engine"]
    stats = fleet_stats(engine, design=design, country=country)
    if not stats or stats.get("count") == 0:
        console.print("[yellow]No boats found.[/yellow]")
        return

    label = []
    if design:
        label.append(design)
    if country:
        label.append(country)
    title = f"Fleet Stats: {' / '.join(label)}" if label else "Full Fleet Stats"

    console.print(f"\n[bold]{title}[/bold]")
    console.print(f"  Boats:        {stats['count']}")
    console.print(f"  TCC Range:    {stats['min_tcc']} - {stats['max_tcc']}")
    console.print(f"  TCC Mean:     {stats['avg_tcc']:.4f}")
    console.print(f"  TCC Median:   {stats['median_tcc']:.4f}")
    console.print(f"  Avg DLR:      {stats['avg_dlr']:.0f}")
    console.print(f"  Avg Headsails: {stats['avg_headsails']:.1f}")
    console.print(f"  Avg Spinnakers: {stats['avg_spinnakers']:.1f}")


@analyze.command(name="diff")
@click.argument("date1")
@click.argument("date2")
@click.pass_context
def analyze_diff(ctx, date1, date2):
    """Compare TCC snapshots between two dates to detect changes."""
    from irc_data.analysis.compare import tcc_snapshot_diff

    engine = ctx.obj["engine"]
    changes = tcc_snapshot_diff(engine, date1, date2)

    if not changes:
        console.print("[green]No TCC changes detected between those dates.[/green]")
        return

    console.print(f"\n[bold]TCC Changes: {date1} → {date2} ({len(changes)} boats)[/bold]")
    diff_table = Table()
    diff_table.add_column("Boat", style="bold")
    diff_table.add_column("Sail No")
    diff_table.add_column("Design")
    diff_table.add_column("Old TCC", justify="right")
    diff_table.add_column("New TCC", justify="right")
    diff_table.add_column("Delta", justify="right")
    for c in changes:
        delta = c["tcc_delta"]
        style = "red" if delta > 0 else "green"
        diff_table.add_row(
            c["boat_name"],
            c["sail_number"],
            c.get("design") or "-",
            str(c["tcc_old"]),
            str(c["tcc_new"]),
            f"[{style}]{delta:+.4f}[/{style}]",
        )
    console.print(diff_table)


@analyze.command(name="regression")
@click.argument("design", required=False)
@click.option("--all", "run_all", is_flag=True, help="Run for all eligible design classes")
@click.option("--min-boats", default=5, help="Minimum boats per design (for --all)")
@click.pass_context
def analyze_regression(ctx, design, run_all, min_boats):
    """Run within-class measurement sensitivity analysis (Engine 1)."""
    from irc_data.analysis.regression import analyze_all_designs, analyze_design_sensitivity

    engine = ctx.obj["engine"]

    if run_all:
        console.print("[bold]Running regression for all eligible designs...[/bold]")
        results = analyze_all_designs(engine, min_boats=min_boats)
        table = Table(title=f"Regression Results ({len(results)} designs)")
        table.add_column("Design", style="bold")
        table.add_column("Tier")
        table.add_column("N", justify="right")
        table.add_column("R²", justify="right")
        table.add_column("CV R²", justify="right")
        table.add_column("Top Lever")
        table.add_column("2nd Lever")

        for r in results:
            coefs = r.get("coefficients", [])
            top = coefs[0]["field"] if coefs else "-"
            second = coefs[1]["field"] if len(coefs) > 1 else "-"
            table.add_row(
                r.get("design", "?"),
                r.get("model_tier", "?"),
                str(r.get("n_boats", 0)),
                f"{r['r_squared']:.3f}" if r.get("r_squared") else "-",
                f"{r['r_squared_cv']:.3f}" if r.get("r_squared_cv") else "-",
                top,
                second,
            )
        console.print(table)
        return

    if not design:
        console.print("[yellow]Specify a design name or use --all[/yellow]")
        return

    result = analyze_design_sensitivity(engine, design)
    if result is None:
        console.print(f"[yellow]Not enough data for '{design}' (need ≥2 boats)[/yellow]")
        return

    d = result.to_dict()
    console.print(f"\n[bold]{design}[/bold] — Tier {d['model_tier']}, n={d['n_boats']}, R²={d.get('r_squared', 0):.3f}")
    if d.get("r_squared_cv"):
        console.print(f"  Cross-validated R²: {d['r_squared_cv']:.3f}, alpha={d.get('alpha', 0):.2f}")

    if d.get("coefficients"):
        table = Table(title="Measurement Sensitivity (ranked)")
        table.add_column("Rank", justify="right")
        table.add_column("Field", style="bold")
        table.add_column("Std Beta", justify="right")
        table.add_column("Impact/Unit", justify="right")
        table.add_column("Unit")
        for c in d["coefficients"]:
            style = "green" if c["std_beta"] < 0 else "red"
            table.add_row(
                str(c["rank"]),
                c["field"],
                f"[{style}]{c['std_beta']:+.4f}[/{style}]",
                f"{c['beta_per_unit']:+.5f}",
                c["unit"],
            )
        console.print(table)

    if d.get("collinearity_warnings"):
        console.print("\n[yellow]Collinearity warnings:[/yellow]")
        for w in d["collinearity_warnings"]:
            console.print(f"  {w}")

    if d.get("interpretation"):
        console.print(f"\n{d['interpretation']}")

    if d.get("correlations"):
        console.print("\n[bold]Pairwise Correlations (too few boats for regression):[/bold]")
        for field, corr in sorted(d["correlations"].items(), key=lambda x: abs(x[1]), reverse=True):
            console.print(f"  {field}: {corr:+.3f}")


@analyze.command(name="drift")
@click.option("--design", "-d", default=None, help="Filter to a specific design class")
@click.pass_context
def analyze_drift(ctx, design):
    """Show IRC formula drift analysis (Engine 2)."""
    from irc_data.analysis.temporal import analyze_fleet_drift

    engine = ctx.obj["engine"]
    result = analyze_fleet_drift(engine, design=design)

    if result is None:
        console.print("[yellow]No drift data available (need boats with multiple TCC snapshots).[/yellow]")
        return

    d = result.to_dict()
    fw = d["fleet_wide"]

    label = f" ({design})" if design else " (all designs)"
    console.print(f"\n[bold]IRC Formula Drift{label}[/bold]")
    console.print(f"  Period: {d['period']}")
    console.print(f"  Stable boats: {fw['n_stable']} of {fw['n_total']}")
    console.print(f"  Mean drift: {fw['mean_drift']:+.5f}")
    console.print(f"  Median drift: {fw['median_drift']:+.5f}")
    console.print(f"  Decreased: {fw['pct_decreased']:.0f}%")

    if fw.get("p_value_ttest") is not None:
        console.print(f"  t-test p-value: {fw['p_value_ttest']:.6f}")
    if fw.get("cohens_d") is not None:
        console.print(f"  Cohen's d: {fw['cohens_d']:.3f}")

    console.print(f"\n  {fw['interpretation']}")

    if d.get("by_dimension"):
        console.print("\n[bold]Dimensional Changes:[/bold]")
        for dim in d["by_dimension"]:
            console.print(f"  {dim['field']}: {dim['coefficient_change']:+.4f} ({dim['direction']})")

    if d.get("by_country"):
        console.print("\n[bold]By Country:[/bold]")
        table = Table()
        table.add_column("Country")
        table.add_column("N Stable", justify="right")
        table.add_column("Mean Drift", justify="right")
        table.add_column("% Decreased", justify="right")
        for country, stats in sorted(d["by_country"].items(), key=lambda x: x[1]["mean_drift"]):
            table.add_row(
                country,
                str(stats["n_stable"]),
                f"{stats['mean_drift']:+.5f}",
                f"{stats['pct_decreased']:.0f}%",
            )
        console.print(table)


@analyze.command(name="performance")
@click.argument("sail_number")
@click.pass_context
def analyze_performance(ctx, sail_number):
    """Show RAI + head-to-head for a boat (Engine 3)."""
    from irc_data.analysis.performance import compute_head_to_head, compute_rai
    from irc_data.db.operations import find_boat_by_sail_number

    engine = ctx.obj["engine"]
    boat_id = find_boat_by_sail_number(engine, sail_number)
    if not boat_id:
        console.print(f"[red]No boat found with sail number '{sail_number}'[/red]")
        return

    # RAI
    rai = compute_rai(engine, boat_id)
    if rai:
        style = "green" if rai.rai > 0 else "red"
        console.print(f"\n[bold]{rai.boat_name} ({rai.sail_number})[/bold]")
        console.print(f"  Design: {rai.design or 'Unknown'}")
        console.print(f"  RAI: [{style}]{rai.rai:+.1f}[/{style}] (95% CI: [{rai.ci_lower:+.1f}, {rai.ci_upper:+.1f}])")
        console.print(f"  Races: {rai.n_races} | Wins: {rai.wins} | Podiums: {rai.podiums}")
        console.print(f"  Avg finish %: {rai.avg_finish_pct:.1%} | Expected: {rai.avg_expected_pct:.1%}")
        console.print(f"\n  {rai.interpretation}")
    else:
        console.print(f"[yellow]No race results found for sail number '{sail_number}'[/yellow]")
        return

    # Head-to-head
    rivals = compute_head_to_head(engine, boat_id)
    if rivals:
        console.print(f"\n[bold]Head-to-Head Records ({len(rivals)} rivals):[/bold]")
        table = Table()
        table.add_column("Rival", style="bold")
        table.add_column("Sail No")
        table.add_column("W", justify="right")
        table.add_column("L", justify="right")
        table.add_column("Win%", justify="right")
        table.add_column("Meetings", justify="right")
        for r in rivals[:20]:
            total = r.wins + r.losses
            pct = r.wins / total * 100 if total > 0 else 0
            style = "green" if pct > 50 else "red" if pct < 50 else ""
            table.add_row(
                r.rival_name,
                r.rival_sail_number,
                str(r.wins),
                str(r.losses),
                f"[{style}]{pct:.0f}%[/{style}]" if style else f"{pct:.0f}%",
                str(r.events_together),
            )
        console.print(table)
    else:
        console.print("  [dim]No head-to-head records (no shared events with other boats)[/dim]")


@analyze.command(name="optimize")
@click.argument("sail_number")
@click.pass_context
def analyze_optimize(ctx, sail_number):
    """Full optimisation report for a boat (Engine 4)."""
    from irc_data.analysis.optimizer import generate_optimisation_report
    from irc_data.db.operations import find_boat_by_sail_number

    engine = ctx.obj["engine"]
    boat_id = find_boat_by_sail_number(engine, sail_number)
    if not boat_id:
        console.print(f"[red]No boat found with sail number '{sail_number}'[/red]")
        return

    report = generate_optimisation_report(engine, boat_id)
    if not report:
        console.print("[yellow]Could not generate report.[/yellow]")
        return

    console.print(f"\n[bold]Optimisation Report: {report.boat_name} ({report.sail_number})[/bold]")
    console.print(f"  Design: {report.design or 'Unknown'}")
    console.print(f"  Current TCC: {report.current_tcc:.4f}" if report.current_tcc else "  Current TCC: —")
    if report.model_tier:
        console.print(f"  Model: Tier {report.model_tier} (R²={report.r_squared:.3f})" if report.r_squared else f"  Model: Tier {report.model_tier}")
    if report.rai is not None:
        console.print(f"  RAI: {report.rai:+.1f}")
    if report.drift_context:
        console.print(f"  Drift: {report.drift_context}")

    if report.recommendations:
        console.print(f"\n[bold]Recommendations ({len(report.recommendations)}):[/bold]")
        table = Table()
        table.add_column("#", justify="right")
        table.add_column("Field", style="bold")
        table.add_column("Category")
        table.add_column("Current", justify="right")
        table.add_column("Target", justify="right")
        table.add_column("Est. TCC", justify="right")
        table.add_column("Feasibility")
        table.add_column("Evidence")

        for rec in report.recommendations:
            target = rec.smart_boat_avg if rec.smart_boat_avg is not None else rec.class_mean
            style = "green" if rec.estimated_tcc_delta < 0 else "red"
            table.add_row(
                str(rec.rank),
                rec.field,
                rec.category,
                f"{rec.current_value:.2f}" if rec.current_value is not None else "—",
                f"{target:.2f}" if target is not None else "—",
                f"[{style}]{rec.estimated_tcc_delta:+.4f}[/{style}]",
                rec.feasibility_label,
                rec.evidence_strength,
            )
        console.print(table)

        console.print("\n[bold]Details:[/bold]")
        for rec in report.recommendations[:5]:
            console.print(f"  {rec.rank}. {rec.explanation}")
    else:
        console.print("  [dim]No recommendations available (insufficient fleet data)[/dim]")

    if report.orc_context:
        console.print(f"\n[bold]ORC Cross-Reference:[/bold]")
        orc = report.orc_context
        if orc.get("gph"):
            console.print(f"  GPH: {orc['gph']:.2f} sec/mile")
        if orc.get("triple_low"):
            console.print(f"  Triple: {orc.get('triple_low', 0):.1f}/{orc.get('triple_med', 0):.1f}/{orc.get('triple_high', 0):.1f}")


@analyze.command(name="compare")
@click.argument("design1")
@click.argument("design2")
@click.pass_context
def analyze_compare(ctx, design1, design2):
    """Cross-design comparison (Engine 5)."""
    from irc_data.analysis.design_compare import compare_designs

    engine = ctx.obj["engine"]
    result = compare_designs(engine, [design1, design2])

    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        return

    for profile in result.get("profiles", []):
        tcc = profile.get("tcc", {})
        perf = profile.get("performance", {})
        activity = profile.get("activity", {})

        console.print(f"\n[bold]{profile['design']}[/bold] ({profile['n_boats']} boats)")
        console.print(f"  TCC: {tcc.get('mean', '—')} (range {tcc.get('min', '—')}–{tcc.get('max', '—')}, spread {tcc.get('spread', '—')})")
        console.print(f"  LOA: {profile.get('rating_efficiency', {}).get('avg_loa', '—')}m")
        console.print(f"  Modification potential: {profile.get('modification_potential', '—')}")
        console.print(f"  Countries: {profile.get('n_countries', 0)}")
        console.print(f"  Race results: {activity.get('total_race_results', 0)} ({activity.get('avg_races_per_boat', 0):.1f}/boat)")

        if perf.get("mean_rai") is not None:
            console.print(f"  Mean RAI: {perf['mean_rai']:+.1f} ({perf.get('n_with_races', 0)} boats with races)")

    if result.get("highlights"):
        console.print("\n[bold]Comparison Highlights:[/bold]")
        for h in result["highlights"]:
            console.print(f"  • {h}")


# --- Database management commands ---


def _get_alembic_cfg(engine_url: str | None = None):
    """Build an Alembic Config using the project's alembic.ini."""
    from alembic.config import Config

    from irc_data.config import DATABASE_URL, PROJECT_ROOT

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    if engine_url:
        alembic_cfg.set_main_option("sqlalchemy.url", engine_url)
    return alembic_cfg


@cli.command(name="db-upgrade")
@click.option("--revision", default="head", help="Target revision (default: head)")
@click.pass_context
def db_upgrade(ctx, revision):
    """Run Alembic migrations to upgrade the database schema."""
    from alembic import command

    alembic_cfg = _get_alembic_cfg()
    console.print(f"Upgrading database to revision: {revision}")
    command.upgrade(alembic_cfg, revision)
    console.print("[green]Database upgrade complete.[/green]")


@cli.command(name="db-stamp")
@click.option("--revision", default="head", help="Revision to stamp (default: head)")
@click.pass_context
def db_stamp(ctx, revision):
    """Stamp the database with a specific Alembic revision (no migration run)."""
    from alembic import command

    alembic_cfg = _get_alembic_cfg()
    console.print(f"Stamping database at revision: {revision}")
    command.stamp(alembic_cfg, revision)
    console.print("[green]Database stamped.[/green]")


@cli.command(name="db-verify-migrations")
@click.pass_context
def db_verify_migrations(ctx):
    """Verify the canonical migration chain (DP-03-05).

    Provisions a throwaway database, exercises upgrade-from-previous-schema on
    a production-sized synthetic dataset, validates counts/hashes/queries,
    checks the time budget, and tests the rollback/restore path.  Never touches
    the configured database.
    """
    from irc_data.db import migration_verify as mv

    console.print("Running DP-03-05 migration verification (throwaway DB)…")
    try:
        ev = mv.run_full_verification()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Verification error: {exc}[/red]")
        raise SystemExit(2)
    console.print(f"  heads: {ev.heads}  linear={ev.linear}")
    console.print(f"  migration_seconds={ev.migration_seconds:.2f} (budget {ev.budget_seconds:.0f})")
    console.print(f"  counts_match={ev.counts_match} hashes_match={ev.hashes_match}")
    console.print(f"  rollback_ok={ev.rollback_ok}")
    console.print(f"  total rows seeded: {sum(ev.seeded_counts.values())}")
    if ev.passed():
        console.print("[green]RESULT: PASS[/green]")
    else:
        console.print("[red]RESULT: FAIL[/red]")
        raise SystemExit(1)


@cli.command(name="import-results")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--format", "fmt", type=click.Choice(["sailsys-json"]), default="sailsys-json", help="JSON format")
@click.pass_context
def import_results(ctx, path: Path, fmt: str):
    """Import race results from a JSON file into the database."""
    engine = ctx.obj["engine"]

    if fmt == "sailsys-json":
        from irc_data.scrapers.result_import import import_sailsys_json

        console.print(f"Importing SailSys results from {path}...")
        stats = import_sailsys_json(engine, path)
        console.print(f"  Total records: {stats['total']}")
        console.print(f"  Imported: {stats['imported']}")
        console.print(f"  Matched to boats: {stats['matched']}")
        console.print(f"  Skipped (no name): {stats['skipped_no_name']}")
        if stats['errors']:
            console.print(f"  [yellow]Errors: {stats['errors']}[/yellow]")


@cli.command(name="dedup-orc-snapshots")
@click.option("--batch-size", default=500, show_default=True, help="Rows per delete batch")
@click.pass_context
def dedup_orc_snapshots(ctx, batch_size):
    """Collapse byte-identical ORC certificate snapshots into a single row per run.

    For each (ref_no, country_id), keeps the earliest row of each
    content-identical run and deletes the rest. Idempotent and safe to re-run.
    """
    from irc_data.db.operations import dedup_orc_certificates

    engine = ctx.obj["engine"]
    console.print("Scanning orc_certificates for duplicate snapshots...")

    def _progress(groups_done: int, groups_total: int, deleted: int):
        console.print(
            f"  {groups_done}/{groups_total} groups scanned — {deleted} duplicates deleted so far"
        )

    stats = dedup_orc_certificates(engine, batch_size=batch_size, progress_callback=_progress)

    console.print(f"\n[green]ORC snapshot dedup complete:[/green]")
    console.print(f"  Groups scanned: {stats['groups_scanned']}")
    console.print(f"  Rows before:    {stats['rows_before']}")
    console.print(f"  Rows after:     {stats['rows_after']}")
    console.print(f"  Rows deleted:   {stats['rows_deleted']}")


@cli.group("report")
def report_group():
    """Diagnostic reports (orphans, coverage)."""


@report_group.command("orc-orphans")
@click.pass_context
def report_orc_orphans(ctx):
    """ORC certs that haven't matched to an IRC boat, by country + reason."""
    from irc_data.diagnostics.orc_reports import orphans_report

    engine = ctx.obj["engine"]
    by_country, reasons = orphans_report(engine)

    console.print("[bold]=== ORC orphans by country ===[/bold]")
    if not by_country:
        console.print("  [green]No orphans.[/green]")
    else:
        for row in by_country:
            console.print(f"  {row.country_id or '(no country)':6}  {row.orphans}")

    console.print("\n[bold]=== Top match-failure reasons (last 7 days) ===[/bold]")
    if not reasons:
        console.print(
            "  [yellow](no rows — match-boats hasn't logged any orphans yet)[/yellow]"
        )
    else:
        for row in reasons:
            console.print(f"  {row.n:4d}  {row.reason}")


@report_group.command("orc-detail-coverage")
@click.pass_context
def report_orc_detail_coverage(ctx):
    """How many ORC certs still lack GPH/CDL/allowances, by country."""
    from irc_data.diagnostics.orc_reports import detail_coverage_report

    engine = ctx.obj["engine"]
    rows = detail_coverage_report(engine)
    console.print(
        f"{'country':10}  {'total':>6}  {'with detail':>12}  {'missing':>8}"
    )
    if not rows:
        console.print("  [yellow](no ORC certs in DB)[/yellow]")
        return
    for row in rows:
        console.print(
            f"{(row.country_id or '(none)'):10}  "
            f"{row.total:6d}  "
            f"{row.with_detail:12d}  "
            f"{row.missing_detail:8d}"
        )


@cli.command(name="refresh-views")
@click.pass_context
def refresh_views(ctx):
    """Refresh all materialized views."""
    from irc_data.db.operations import refresh_materialized_views

    engine = ctx.obj["engine"]
    console.print("Refreshing materialized views...")
    refreshed = refresh_materialized_views(engine)
    for view in refreshed:
        console.print(f"  [green]Refreshed: {view}[/green]")
    if not refreshed:
        console.print("  [yellow]No views refreshed (do they exist?)[/yellow]")


@cli.command(name="match-boats")
@click.option("--dry-run", is_flag=True, help="Show matches without writing to DB")
@click.option(
    "--orc-only",
    is_flag=True,
    help=(
        "Fast path for daily cron: match ORC certs to boats and record ORC "
        "identities only. Skips IRC-side identity recording and design "
        "backfills (which are slower and don't change between ORC scrapes)."
    ),
)
@click.pass_context
def match_boats(ctx, dry_run, orc_only):
    """Match ORC certificates to IRC boats by sail number and name."""
    from irc_data.matching.identity import (
        backfill_boat_details_from_orc,
        backfill_design_from_irc_certs,
        backfill_design_from_sailsys,
        match_orc_to_irc,
        record_identities_from_irc,
        record_identities_from_orc,
    )

    engine = ctx.obj["engine"]

    console.print("[bold]Step 1:[/bold] Matching ORC certificates to IRC boats...")
    stats = match_orc_to_irc(engine, dry_run=dry_run)
    console.print(f"  Total ORC certs (unmatched): {stats['total_orc']}")
    console.print(f"  Already matched: {stats['already_matched']}")
    console.print(f"  Matched by sail number: {stats['matched_sail_exact']}")
    console.print(f"  Matched by sail + name: {stats['matched_sail_name']}")
    console.print(f"  Matched by name + country: {stats['matched_name_country']}")
    console.print(f"  Ambiguous skipped: {stats['ambiguous_skipped']}")
    console.print(f"  [green]Total matched: {stats['matched_total']}[/green]")
    console.print(f"  Unmatched: {stats['unmatched']}")

    if dry_run:
        console.print("[yellow]Dry run — no changes written.[/yellow]")
        return

    console.print("\n[bold]Step 2:[/bold] Recording identity observations...")
    if not orc_only:
        irc_ids = record_identities_from_irc(engine)
        console.print(f"  IRC identities recorded: {irc_ids}")
    orc_ids = record_identities_from_orc(engine)
    console.print(f"  ORC identities recorded: {orc_ids}")

    console.print("\n[bold]Step 3:[/bold] Backfilling boat details from ORC...")
    backfilled = backfill_boat_details_from_orc(engine)
    console.print(f"  Boats updated with ORC data: {backfilled}")

    if orc_only:
        console.print(
            "\n[yellow]--orc-only: skipping IRC cert + SailSys design backfill.[/yellow]"
        )
        return

    console.print("\n[bold]Step 4:[/bold] Backfilling design from IRC certificates...")
    irc_design_count = backfill_design_from_irc_certs(engine)
    console.print(f"  Boats updated with IRC cert design: {irc_design_count}")

    console.print("\n[bold]Step 5:[/bold] Backfilling design from SailSys race data...")
    sailsys_design_count = backfill_design_from_sailsys(engine)
    console.print(f"  Boats updated with SailSys design: {sailsys_design_count}")


@cli.command(name="seed-designs")
@click.pass_context
def seed_designs(ctx):
    """Seed design_classes from IRC + ORC data and backfill design_canonical."""
    from irc_data.matching.designs import backfill_design_canonical, seed_design_classes

    engine = ctx.obj["engine"]

    console.print("[bold]Step 1:[/bold] Seeding design classes...")
    stats = seed_design_classes(engine)
    console.print(f"  From IRC: {stats['from_irc']} designs")
    console.print(f"  From ORC: {stats['from_orc']} designs")
    console.print(f"  Total design classes: {stats['total']}")

    console.print("\n[bold]Step 2:[/bold] Backfilling design_canonical on boats...")
    updated = backfill_design_canonical(engine)
    console.print(f"  Boats updated: {updated}")


@cli.command(name="seed-design-designers")
@click.pass_context
def seed_design_designers(ctx):
    """Apply the curated design -> designer/builder mapping to design_classes.

    Idempotent: only fills NULL/empty values in design_classes; existing
    non-empty designer/builder values are left untouched.
    """
    from irc_data.matching.design_designers import DESIGN_DESIGNERS

    engine = ctx.obj["engine"]
    total = len(DESIGN_DESIGNERS)
    designer_updates = 0
    builder_updates = 0
    designer_skipped = 0
    builder_skipped = 0
    missing_classes = 0

    with engine.begin() as conn:
        for name_canonical, (designer, builder) in DESIGN_DESIGNERS.items():
            row = conn.execute(
                text(
                    "SELECT id, designer, builder FROM design_classes "
                    "WHERE name_canonical = :n"
                ),
                {"n": name_canonical},
            ).fetchone()
            if not row:
                missing_classes += 1
                continue

            if designer:
                if row.designer is None or row.designer.strip() == "":
                    conn.execute(
                        text("UPDATE design_classes SET designer = :d WHERE id = :id"),
                        {"d": designer, "id": row.id},
                    )
                    designer_updates += 1
                else:
                    designer_skipped += 1
            if builder:
                if row.builder is None or row.builder.strip() == "":
                    conn.execute(
                        text("UPDATE design_classes SET builder = :b WHERE id = :id"),
                        {"b": builder, "id": row.id},
                    )
                    builder_updates += 1
                else:
                    builder_skipped += 1

    console.print("[bold]Seed design_classes designer/builder:[/bold]")
    console.print(f"  Curated entries:        {total}")
    console.print(f"  Designer rows updated:  {designer_updates}")
    console.print(f"  Designer rows skipped:  {designer_skipped} (already set)")
    console.print(f"  Builder rows updated:   {builder_updates}")
    console.print(f"  Builder rows skipped:   {builder_skipped} (already set)")
    console.print(
        f"  Missing from design_classes: {missing_classes} "
        "(no matching name_canonical; rerun `seed-designs` first if many)"
    )


@cli.command(name="backfill-boat-identity")
@click.option(
    "--source",
    type=click.Choice(["all", "orc", "design_classes"]),
    default="all",
    help="Limit backfill to a single source.",
)
@click.pass_context
def backfill_boat_identity(ctx, source):
    """Backfill boats.designer / builder / year_built from authoritative sources.

    Priority order (each source only fills currently-NULL columns):
      1. orc_certificates (per-boat owner-declared values; latest snapshot wins)
      2. design_classes (curated mapping, via boats.design_canonical)

    Idempotent — re-running on a clean DB updates 0 rows.
    """
    engine = ctx.obj["engine"]

    def coverage():
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) AS total, "
                "       COUNT(designer) AS d, "
                "       COUNT(builder) AS b, "
                "       COUNT(year_built) AS y "
                "FROM boats"
            )).fetchone()
        return dict(total=row.total, designer=row.d, builder=row.b, year=row.y)

    before = coverage()
    console.print(
        f"[bold]Before:[/bold] boats={before['total']} "
        f"designer={before['designer']} ({before['designer']*100/before['total']:.1f}%) "
        f"builder={before['builder']} ({before['builder']*100/before['total']:.1f}%) "
        f"year_built={before['year']} ({before['year']*100/before['total']:.1f}%)"
    )

    results: dict[str, dict[str, int]] = {}

    # --- Source 1: ORC certificates ---
    # Per-field "latest non-empty" wins. ORC snapshots are inconsistent —
    # often the most recent row has blank designer/builder but an older
    # snapshot has the real data — so we don't just pick max(snapshot_date).
    if source in ("all", "orc"):
        with engine.begin() as conn:
            r = conn.execute(text("""
                WITH src AS (
                    SELECT
                        boat_id,
                        (ARRAY_REMOVE(ARRAY_AGG(
                            NULLIF(designer, '')
                            ORDER BY snapshot_date DESC, id DESC
                        ), NULL))[1] AS designer,
                        (ARRAY_REMOVE(ARRAY_AGG(
                            NULLIF(builder, '')
                            ORDER BY snapshot_date DESC, id DESC
                        ), NULL))[1] AS builder,
                        (ARRAY_REMOVE(ARRAY_AGG(
                            year_built
                            ORDER BY snapshot_date DESC, id DESC
                        ), NULL))[1] AS year_built
                    FROM orc_certificates
                    WHERE boat_id IS NOT NULL
                    GROUP BY boat_id
                )
                UPDATE boats b
                SET designer   = COALESCE(NULLIF(b.designer, ''), src.designer),
                    builder    = COALESCE(NULLIF(b.builder, ''),  src.builder),
                    year_built = COALESCE(b.year_built,            src.year_built)
                FROM src
                WHERE b.id = src.boat_id
                  AND (
                    (NULLIF(b.designer, '') IS NULL AND src.designer IS NOT NULL)
                    OR (NULLIF(b.builder, '') IS NULL AND src.builder IS NOT NULL)
                    OR (b.year_built IS NULL AND src.year_built IS NOT NULL)
                  )
                RETURNING b.id,
                          (src.designer IS NOT NULL) AS got_d,
                          (src.builder  IS NOT NULL) AS got_b,
                          (src.year_built IS NOT NULL) AS got_y
            """)).fetchall()
        results["orc"] = {
            "rows_touched": len(r),
            "designer_filled": sum(1 for x in r if x.got_d),
            "builder_filled":  sum(1 for x in r if x.got_b),
            "year_filled":     sum(1 for x in r if x.got_y),
        }
        after_orc = coverage()
        console.print(
            f"  [green]ORC:[/green] rows touched={results['orc']['rows_touched']} "
            f"(+designer {after_orc['designer']-before['designer']}, "
            f"+builder {after_orc['builder']-before['builder']}, "
            f"+year {after_orc['year']-before['year']})"
        )
    else:
        after_orc = before

    # --- Source 2: design_classes (curated via design_canonical) ---
    if source in ("all", "design_classes"):
        with engine.begin() as conn:
            r = conn.execute(text("""
                UPDATE boats b
                SET designer = COALESCE(NULLIF(b.designer, ''), dc.designer),
                    builder  = COALESCE(NULLIF(b.builder, ''),  dc.builder)
                FROM design_classes dc
                WHERE b.design_canonical = dc.name_canonical
                  AND (
                    (NULLIF(b.designer, '') IS NULL AND dc.designer IS NOT NULL AND dc.designer <> '')
                    OR (NULLIF(b.builder, '') IS NULL AND dc.builder IS NOT NULL AND dc.builder <> '')
                  )
                RETURNING b.id,
                          (dc.designer IS NOT NULL AND dc.designer <> '') AS got_d,
                          (dc.builder  IS NOT NULL AND dc.builder  <> '') AS got_b
            """)).fetchall()
        results["design_classes"] = {
            "rows_touched": len(r),
            "designer_filled": sum(1 for x in r if x.got_d),
            "builder_filled":  sum(1 for x in r if x.got_b),
        }
        after_dc = coverage()
        console.print(
            f"  [green]design_classes:[/green] rows touched={results['design_classes']['rows_touched']} "
            f"(+designer {after_dc['designer']-after_orc['designer']}, "
            f"+builder {after_dc['builder']-after_orc['builder']})"
        )

    after = coverage()
    console.print(
        f"[bold]After:[/bold]  boats={after['total']} "
        f"designer={after['designer']} ({after['designer']*100/after['total']:.1f}%) "
        f"builder={after['builder']} ({after['builder']*100/after['total']:.1f}%) "
        f"year_built={after['year']} ({after['year']*100/after['total']:.1f}%)"
    )
    console.print(
        f"[bold]Delta:[/bold]  designer +{after['designer']-before['designer']}, "
        f"builder +{after['builder']-before['builder']}, "
        f"year_built +{after['year']-before['year']}"
    )


@cli.command(name="health-check")
@click.option("--notify", is_flag=True, help="Post the daily report to the Slack/webhook channel (SLACK_WEBHOOK_URL or WEBHOOK_URL)")
@click.option("--webhook-url", envvar="WEBHOOK_URL", default=None, help="Discord/Slack webhook URL")
@click.option("--deadman-url", envvar="DEADMAN_PING_URL", default=None,
              help="Dead-man ping URL; the external monitor alerts if no ping lands by 09:30 UTC")
@click.option("--no-deadman", is_flag=True, help="Do not ping the dead-man URL this run")
@click.pass_context
def health_check(ctx, notify, webhook_url, deadman_url, no_deadman):
    """Run health checks, optionally notify, and ping the dead-man URL.

    OPS-02-03 daily heartbeat. Run once a day from cron (before 09:30 UTC):

    * with ``--notify`` the report is posted to the configured Slack/Discord
      webhook (and the legacy WEBHOOK_URL path) so a human sees the platform
      is alive every morning;
    * the **dead-man ping** GETs ``$DEADMAN_PING_URL``. The external monitor
      behind that URL is configured to raise an alert if no ping has arrived
      by **09:30 UTC** — so if this cron job itself is dead (the silent-
      failure mode that produced no output at all), someone is paged the
      next morning.

    Secrets (webhook + dead-man URLs) come from the 1Password vault via
    ``op run`` — see :mod:`irc_data.alerting`.
    """
    import os as _os

    from irc_data import alerting
    from irc_data.monitoring import check_health, send_webhook

    engine = ctx.obj["engine"]
    report = check_health(engine)

    # Display
    status_color = "green" if report["status"] == "ok" else "red"
    console.print(f"\n[{status_color}]Status: {report['status'].upper()}[/{status_color}]")

    checks = report.get("checks", {})
    if "counts" in checks:
        for k, v in checks["counts"].items():
            console.print(f"  {k}: {v}")

    if checks.get("orc_latest"):
        console.print(f"  ORC latest: {checks['orc_latest']} ({checks.get('hours_since_orc', '?')}h ago)")
    if checks.get("irc_latest"):
        console.print(f"  IRC latest: {checks['irc_latest']} ({checks.get('days_since_irc', '?')}d ago)")
    if checks.get("disk_usage_pct"):
        console.print(f"  Disk usage: {checks['disk_usage_pct']}%")

    alerts = report.get("alerts", [])
    if alerts:
        console.print(f"\n[yellow]Alerts ({len(alerts)}):[/yellow]")
        for a in alerts:
            console.print(f"  [yellow]! {a}[/yellow]")
    else:
        console.print("\n  No alerts")

    # --- Notify: post the daily report to Slack (and legacy webhook) -----
    if notify:
        # Prefer the dedicated Slack webhook, falling back to WEBHOOK_URL.
        slack_url = (
            _os.environ.get(alerting.SLACK_WEBHOOK_ENV)
            or webhook_url
            or _os.environ.get(alerting.SLACK_WEBHOOK_FALLBACK_ENV)
        )
        if slack_url:
            console.print("\nPosting daily report to webhook...")
            ok = send_webhook(slack_url, report)
            if ok:
                console.print("[green]  Webhook notification sent.[/green]")
            else:
                console.print("[red]  Webhook notification failed.[/red]")
        else:
            console.print("[yellow]  --notify set but no SLACK_WEBHOOK_URL/WEBHOOK_URL configured[/yellow]")

    # --- Dead-man ping: prove the cron is alive ---------------------------
    if not no_deadman:
        deadman_url = deadman_url or _os.environ.get(alerting.DEADMAN_URL_ENV)
        if deadman_url:
            ok = alerting.ping_deadman(deadman_url)
            if ok:
                console.print("[green]  Dead-man ping sent (monitor resets; alerts if missed by 09:30 UTC).[/green]")
            else:
                console.print("[red]  Dead-man ping FAILED — the external monitor will alert if unrecovered.[/red]")
        else:
            console.print("[dim]  No DEADMAN_PING_URL configured — dead-man ping skipped.[/dim]")


@cli.command(name="scraper-health")
@click.option("--no-alert", is_flag=True, help="Do not send failure alerts (still logs + reports)")
@click.option("--webhook-url", default=None, help="Override Discord/Slack webhook for failure alerts")
@click.option("--alert-email", default=None, help="Override alert recipient email (Resend)")
@click.option("--json-output", is_flag=True, help="Print the report as JSON instead of text")
@click.pass_context
def scraper_health(ctx, no_alert, webhook_url, alert_email, json_output):
    """DP-00-02: daily health check for the four active scrapers.

    Probes TopYacht, SailSys, IRC TCC Listings and ORC for fetch success,
    reports record counts and the last-success timestamp per source, and
    writes one run-log row per source per cycle. Any fetch failure alerts
    within this same cycle (webhook/email) and exits non-zero.
    """
    import json as _json

    from irc_data.scraper_health import format_report, run_health_check

    engine = ctx.obj["engine"]
    report = run_health_check(
        engine,
        alert=not no_alert,
        webhook_url=webhook_url,
        alert_email=alert_email,
    )

    if json_output:
        console.print(_json.dumps(report.to_dict(), indent=2))
    else:
        console.print(format_report(report))

    # Non-zero exit on any failure so cron marks the run failed.
    raise SystemExit(0 if report.ok else 1)


@cli.command(name="rematch-results")
@click.option("--dry-run", is_flag=True, help="Show matches without writing to DB")
@click.pass_context
def rematch_results(ctx, dry_run):
    """Re-match unmatched race results to boats using multiple strategies."""
    from irc_data.matching.results import run_rematch

    engine = ctx.obj["engine"]
    console.print("[bold]Re-matching unmatched race results...[/bold]")
    stats = run_rematch(engine, dry_run=dry_run)

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Total unmatched before: {stats['total_unmatched']}")
    console.print(f"  Matches found:         {stats['matched']}")
    console.print(f"  Updates applied:       {stats['updated']}")
    console.print(f"  Boats created:         {stats.get('boats_created', 0)}")
    console.print(f"  Remaining unmatched:   {stats['remaining']}")
    console.print(f"\n[bold]By strategy:[/bold]")
    for key in sorted(stats):
        if key.startswith(("sailsys_", "rorc_", "generic_")):
            console.print(f"  {key}: {stats[key]}")

    if stats.get("boats_created", 0):
        console.print(f"\n  New boats from results: {stats['boats_created']}")
        console.print(f"  Results linked to new:  {stats.get('results_linked_to_new_boats', 0)}")
    if stats.get("phase3_rematched", 0):
        console.print(f"  Phase 3 re-matches:    {stats['phase3_rematched']}")

    if stats['total_unmatched'] > 0:
        pct = stats['matched'] / stats['total_unmatched'] * 100
        console.print(f"\n  Match rate: [green]{pct:.1f}%[/green]")


@cli.command(name="scrape-daemon")
@click.option("--interval", default=1800, help="Seconds between scrape cycles (default: 1800 = 30min)")
@click.pass_context
def scrape_daemon(ctx, interval):
    """Run continuous scrape cycles: all SailSys clubs + rematch every N seconds."""
    import asyncio
    import time

    from irc_data.db.operations import log_ingestion_end, log_ingestion_start
    from irc_data.matching.results import run_rematch
    from irc_data.scrapers.result_import import import_scraper_results
    from irc_data.scrapers.sailsys import CLUBS, scrape_club_irc_results

    engine = ctx.obj["engine"]
    console.print(f"[bold]Starting scrape daemon (cycle every {interval}s)[/bold]")
    console.print(f"  Clubs: {', '.join(CLUBS.keys())}")

    cycle = 0
    while True:
        cycle += 1
        cycle_start = time.time()
        console.print(f"\n{'='*50}")
        console.print(f"[bold]Cycle {cycle}[/bold] — {time.strftime('%Y-%m-%d %H:%M:%S')}")

        # Determine incremental cutoff
        since = None
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT max(created_at)::date - interval '1 day'
                    FROM race_results WHERE source = 'sailsys'
                """)
            ).first()
            if row and row[0]:
                since = row[0].date() if hasattr(row[0], 'date') else row[0]

        total_results = 0
        total_matched = 0
        errors = []

        for club_name, club_id in CLUBS.items():
            try:
                club_results = asyncio.run(scrape_club_irc_results(
                    club_id, since=since,
                ))
                if club_results:
                    stats = import_scraper_results(
                        engine, club_results, source="sailsys",
                        organizing_club=club_name,
                    )
                    total_results += stats["imported"]
                    total_matched += stats["matched"]
                    console.print(f"  {club_name}: {stats['imported']} results ({stats['matched']} matched)")
                else:
                    console.print(f"  {club_name}: no new results")
            except Exception as e:
                errors.append(f"{club_name}: {e}")
                console.print(f"  [red]{club_name}: {e}[/red]")

        # Run rematch after scrape cycle
        console.print("\n  Running rematch...")
        try:
            rematch_stats = run_rematch(engine)
            console.print(f"  Rematch: {rematch_stats['matched']} new matches")
        except Exception as e:
            console.print(f"  [red]Rematch error: {e}[/red]")

        elapsed = time.time() - cycle_start
        console.print(f"\n  Cycle {cycle} complete in {elapsed:.0f}s — {total_results} results, {total_matched} matched")
        if errors:
            console.print(f"  [yellow]{len(errors)} errors[/yellow]")

        # Sleep until next cycle
        sleep_time = max(0, interval - elapsed)
        if sleep_time > 0:
            console.print(f"  Sleeping {sleep_time:.0f}s until next cycle...")
            time.sleep(sleep_time)


@cli.command(name="serve")
@click.option("--host", default="0.0.0.0", envvar="HOST", help="Bind address")
@click.option("--port", default=4100, type=int, envvar="PORT", help="Port")
@click.option("--workers", default=2, help="Worker processes")
@click.option("--reload", "do_reload", is_flag=True, help="Auto-reload on code changes")
def serve(host, port, workers, do_reload):
    """Start the FastAPI API server."""
    import uvicorn

    console.print(f"Starting API server on {host}:{port} (workers={workers})...")
    uvicorn.run(
        "irc_data.api.app:app",
        host=host,
        port=port,
        workers=1 if do_reload else workers,
        reload=do_reload,
    )


@cli.command(name="discover-events")
@click.option("--url", help="Single URL to crawl + extract (mutually exclusive with --seed-url)")
@click.option("--seed-url", help="Seed URL — Firecrawl maps it then crawls each sub-URL")
@click.option("--limit", type=int, default=30, help="Max sub-URLs per seed (default 30)")
@click.option("--auto-ingest/--no-auto-ingest", default=False,
              help="When confidence ≥ 0.85 and platform is sailsys, ingest immediately")
@click.pass_context
def discover_events(ctx, url, seed_url, limit, auto_ingest):
    """Discover sailing-event scoring URLs via Firecrawl + Claude.

    Either --url (one page) or --seed-url (one page + everything mapped
    from it). Results land in `event_discovery` with status='pending'
    for Justin to review at /justin/discovery.
    """
    from irc_data.discovery.service import discover_url, discover_seed, ingest_confirmed

    engine = ctx.obj["engine"]
    if (url is None) == (seed_url is None):
        console.print("[red]Specify exactly one of --url or --seed-url.[/red]")
        return

    if url:
        rows = [discover_url(engine, url)]
        console.print(f"[green]1 URL processed.[/green]")
    else:
        rows = discover_seed(engine, seed_url, limit=limit)
        console.print(f"[green]{len(rows)} URLs processed from seed.[/green]")

    pending = [r for r in rows if r.get("status") == "pending"]
    failed = [r for r in rows if r.get("status") == "failed"]
    console.print(f"  pending={len(pending)}  failed={len(failed)}")

    if auto_ingest:
        confident = [
            r for r in pending
            if r.get("scoring_platform") == "sailsys"
            and (r.get("confidence") or 0) >= 0.85
            and r.get("platform_ids", {}).get("series_id")
        ]
        for r in confident:
            try:
                out = ingest_confirmed(engine, r["id"])
                console.print(f"  → ingested #{r['id']}: {out}")
            except Exception as e:
                console.print(f"  [red]ingest #{r['id']} failed: {e}[/red]")


@cli.command(name="discover-and-ingest")
@click.option("--seed-url", required=True,
              help="Seed URL — Firecrawl maps it, then race results are "
                   "extracted + imported from every reachable sub-URL.")
@click.option(
    "--source",
    type=click.Choice(["cowesweek", "sydneyhobart", "rhkyc", "isora",
                       "sailracehq", "sailwave", "yachtscoring", "rpayc",
                       "firecrawl"]),
    required=True,
    help="Value written to race_results.source.",
)
@click.option("--max-pages", type=int, default=20,
              help="Cap on how many mapped URLs to crawl per run.")
@click.option("--tag-as", default="firecrawl",
              help="Value written to race_results.transport (typically "
                   "'firecrawl' during parallel-run; can be 'legacy' to "
                   "replay an old scraper through the same pipeline).")
@click.option("--year", type=int, default=None,
              help="Event year — used by per-source expanders (e.g. cowesweek).")
@click.option(
    "--mode",
    type=click.Choice(["map-site", "per-source-expand"]),
    default="map-site",
    help="URL-discovery strategy. 'map-site': Firecrawl maps the seed URL. "
         "'per-source-expand': use the source's registered URL expander "
         "(requires --year for sources like cowesweek).",
)
@click.pass_context
def discover_and_ingest(ctx, seed_url, source, max_pages, tag_as, year, mode):
    """Map a seed URL, extract race results from every page, import them.

    The Firecrawl-based replacement for the bespoke ``scrape results
    --source X`` crons. Each mapped URL is scraped, the markdown is sent
    to Claude's ``extract_results``, and structured rows are inserted via
    ``import_scraper_results`` with the supplied source + transport tag.

    Fails soft per-URL: a single bad page doesn't poison the batch.
    """
    from irc_data.discovery.orchestrator import seed_crawl_and_ingest

    engine = ctx.obj["engine"]
    console.print(
        f"[cyan]discover-and-ingest[/cyan] seed={seed_url} source={source} "
        f"max_pages={max_pages} tag_as={tag_as} mode={mode}"
        + (f" year={year}" if year else "")
    )

    stats = seed_crawl_and_ingest(
        engine,
        seed_url=seed_url,
        source=source,
        max_pages=max_pages,
        transport_tag=tag_as,
        year=year,
        mode=mode,
    )
    console.print(
        f"[green]urls_mapped={stats['urls_mapped']}[/green]  "
        f"with_results={stats['urls_with_results']}  "
        f"failed={stats['urls_failed']}  "
        f"rows_imported={stats['rows_imported']}  "
        f"rows_matched={stats['rows_matched']}"
    )


# Default aggregator seed URLs used by `irc-data seed-crawl --aggregators`.
# Edit here to add/remove top-level sources for the nightly discovery loop.
DEFAULT_AGGREGATORS = [
    "https://www.rya.org.uk/racing/fixtures",
    "https://www.australiansailing.org/events",
    "https://www.rorc.org/events",
]


@cli.command(name="seed-crawl")
@click.option("--aggregators", is_flag=True,
              help="Crawl the built-in list of aggregator/fixture sites and "
                   "queue every discovered URL into event_discovery.")
@click.option("--seed-url", default=None,
              help="Optional override — crawl just this one seed URL.")
@click.option("--limit", type=int, default=50,
              help="Max URLs to map per seed (default 50).")
@click.pass_context
def seed_crawl(ctx, aggregators, seed_url, limit):
    """Nightly seed-crawl. Map aggregator sites → queue URLs in event_discovery.

    Aggregator pages are calendars/fixture lists — not results pages
    themselves — so we use the discovery service (extract_event, not
    extract_results). Rows land in ``event_discovery`` with status='pending'
    for Justin to confirm at /justin/discovery before ingestion.
    """
    from irc_data.discovery.service import discover_seed

    engine = ctx.obj["engine"]
    seeds: list[str] = []
    if aggregators:
        seeds.extend(DEFAULT_AGGREGATORS)
    if seed_url:
        seeds.append(seed_url)
    if not seeds:
        console.print(
            "[red]Pass --aggregators and/or --seed-url URL.[/red]"
        )
        raise SystemExit(2)

    total = 0
    for seed in seeds:
        console.print(f"[cyan]mapping[/cyan] {seed}")
        try:
            rows = discover_seed(engine, seed, limit=limit)
            console.print(f"  {len(rows)} URLs queued / refreshed")
            total += len(rows)
        except Exception as e:
            console.print(f"  [red]failed: {e}[/red]")

    console.print(f"[green]done — {total} URLs total[/green]")


@cli.command(name="solent-coverage")
@click.option(
    "--mode",
    type=click.Choice(["discover", "ingest", "all"]),
    default="all",
    show_default=True,
    help="discover = queue Solent result pages into event_discovery; "
         "ingest = import JOG + Warsash results into race_results; "
         "all = discover then ingest.",
)
@click.option("--year", "years", type=int, multiple=True,
              help="JOG season year (repeatable; default current + previous).")
@click.option("--max-races", type=int, default=None,
              help="Cap on JOG races ingested (useful for canary runs).")
@click.option("--skip-warsash", is_flag=True, help="Skip the Warsash Sailwave ingest.")
@click.option("--skip-jog", is_flag=True, help="Skip the JOG ingest.")
@click.option("--dry-run", is_flag=True,
              help="Discovery only — no content is written to race_results.")
@click.pass_context
def solent_coverage(ctx, mode, years, max_races, skip_warsash, skip_jog, dry_run):
    """OPS-02-14 — UK/Solent coverage: discover + ingest Solent results.

    Goal: results for the boats that pay (Solent, not just Sydney).  This
    command runs the registered Solent sources through the discovery
    pipeline (HRSC / Hamble / Solent series + JOG) and imports results into
    ``race_results`` so the Sun Fast 3300 and J/109 Solent fleets have
    coverage.  Every source is policy-checked before any content is fetched.
    """
    engine = ctx.obj["engine"]

    from irc_data.discovery import solent as solent_mod

    if mode in ("discover", "all"):
        console.print("[cyan]solent-coverage[/cyan] discovery — queueing Solent result pages")
        summary = solent_mod.discover_solent_sources(engine)
        for seed, n in summary.get("sources", {}).items():
            console.print(f"  {n:3d} queued  {seed}")
        for err in summary.get("errors", []):
            console.print(f"  [yellow]{err}[/yellow]")
        console.print(f"[green]discovery done — {summary.get('queued', 0)} URLs queued[/green]")

    if dry_run:
        console.print("[dim]dry-run: skipping ingestion[/dim]")
        return

    if mode in ("ingest", "all"):
        if not skip_jog:
            console.print("[cyan]solent-coverage[/cyan] ingesting JOG seasons "
                          f"({', '.join(map(str, years)) or 'current+previous'})")
            jog = solent_mod.ingest_jog_season(
                engine, years=list(years) or None, max_races=max_races,
            )
            console.print(f"  JOG: {jog['events']} races, "
                          f"{jog['imported']} imported, {jog['matched']} matched")
        if not skip_warsash:
            console.print("[cyan]solent-coverage[/cyan] ingesting Warsash Spring Series (Sailwave)")
            w = solent_mod.ingest_warsash_sailwave(engine)
            console.print(f"  Warsash: {w['files']} files, "
                          f"{w['imported']} imported, {w['matched']} matched")


@cli.command(name="scrape-watchdog")
@click.option("--cooldown-hours", type=int, default=4,
              help="Minimum hours between repeat alerts for the same source.")
@click.option("--dry-run", is_flag=True, help="Print what would alert; don't send email.")
@click.pass_context
def scrape_watchdog(ctx, cooldown_hours, dry_run):
    """Staleness watchdog (OPS-01-04 / OPS-02-03). Cron runs every 15 min.

    Checks every configured source against its OPS-02-03 freshness budget
    (ORC/TCC/TopYacht 26h, SailSys 2h run / 26h data, weekly 8d), raises ONE
    alert per breach fanned out to **Slack + email** (so a single dead
    transport can't silence the page), respects a 4 h cooldown per source,
    sends a recovery message when a source returns, and retains the full
    alert history in the ``watchdog_alerts`` table.
    """
    from irc_data.scrape_watchdog import run_watchdog

    engine = ctx.obj["engine"]

    try:
        result = run_watchdog(
            engine,
            cooldown_hours=cooldown_hours,
            dry_run=dry_run,
        )
    except Exception as e:
        console.print(f"[red]Watchdog failed: {e}[/red]")
        raise SystemExit(1)

    if not result.breaches and not result.recoveries:
        console.print("[green]All scrapers within budget.[/green]")
        return

    if result.breaches:
        console.print(f"[yellow]{len(result.breaches)} scraper(s) stale:[/yellow]")
        for b in result.breaches:
            console.print(
                f"  - {b.label} ({b.source}): {b.age_str()} since last success "
                f"(budget {b.budget_hours:.1f}h, {b.cadence})"
            )

    for b in result.in_cooldown:
        console.print(f"  [dim]  (cooldown active for {b.alert_key}, skipping)[/dim]")

    chan = "+".join(result.channels) if result.channels else "none"

    if result.alerts_sent:
        if result.email_sent or result.slack_sent:
            console.print(f"[green]Alert sent ({chan}) for {len(result.alerts_sent)} breach(es); "
                          f"logged to watchdog_alerts.[/green]")
        else:
            console.print(f"[dim]{len(result.alerts_sent)} breach(es) logged "
                          f"(no channel reached — dry-run or transports disabled).[/dim]")
    elif result.breaches and not result.in_cooldown:
        console.print("[dim]No new alerts to send.[/dim]")

    if result.recoveries:
        names = ", ".join(r["source"] for r in result.recoveries)
        if result.recovery_email_sent or result.recovery_slack_sent:
            console.print(f"[green]Recovery sent ({chan}); alerts cleared for: {names}.[/green]")
        else:
            console.print(f"[green]Alerts cleared for: {names} "
                          f"(no channel reached — dry-run or transports disabled).[/green]")


# ---------------------------------------------------------------------------
# Externally-defined sub-commands.
#
# Diagnostics + discovery commands live in their own modules; we attach them
# to the top-level `cli` group here so they appear in `irc-data --help`.
# ---------------------------------------------------------------------------

from irc_data.diagnostics.scraper_parity import parity_report as _parity_report  # noqa: E402

cli.add_command(_parity_report)

from irc_data.cli_news_events import register_news_and_events_commands
register_news_and_events_commands(cli)

from irc_data.diagnostics.source_monitor_cli import source_monitor as _source_monitor_cli  # noqa: E402

cli.add_command(_source_monitor_cli)

from irc_data.diagnostics.reconciliation_cli import reconcile as _reconcile_cli  # noqa: E402

cli.add_command(_reconcile_cli)

from irc_data.resilience.cli import dr_drill as _dr_drill  # noqa: E402

cli.add_command(_dr_drill)

from irc_data.operations.cli import ops_soak as _ops_soak  # noqa: E402

cli.add_command(_ops_soak)

