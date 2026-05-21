"""Multi-strategy IRC historical certificate backfill orchestrator.

Combines three pre-existing strategies into one pipeline:

1. **TCC CSV-derived URL probing** — :mod:`irc_data.scrapers.historical_certs`
   builds candidate URLs from cert number + boat name + sail number.
2. **Live HEAD/GET** against ``ircrating.org/pdfdirectory/`` for those
   candidates.
3. **Wayback Machine fallback** — :func:`irc_data.scrapers.wayback.lookup_pdf_in_wayback`
   queries the CDX index for any archived snapshot of each candidate URL
   and downloads the most recent one.

State is persisted to ``HISTORICAL_CERTS_DIR/.irc_backfill_state.json`` so
the orchestrator is resumable across sessions.
"""

from __future__ import annotations

import json
from pathlib import Path

from irc_data.config import HISTORICAL_CERTS_DIR
from irc_data.scrapers.base import RateLimiter, get_http_client
from irc_data.scrapers.historical_certs import build_cert_url_variants
from irc_data.scrapers.wayback import lookup_pdf_in_wayback


head_limiter = RateLimiter(min_delay=1.0, jitter=0.5)
download_limiter = RateLimiter(min_delay=1.5, jitter=1.0)


def _state_path() -> Path:
    return HISTORICAL_CERTS_DIR / ".irc_backfill_state.json"


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {"done": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"done": []}
    if "done" not in data or not isinstance(data["done"], list):
        return {"done": []}
    return data


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _safe_filename(cert_number: str) -> str:
    """Make a filesystem-safe component out of a cert number."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cert_number)


def _save_pdf(content: bytes, cert_number: str, prefix: str = "") -> Path:
    HISTORICAL_CERTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{prefix + '_' if prefix else ''}{_safe_filename(cert_number)}.pdf"
    dest = HISTORICAL_CERTS_DIR / name
    dest.write_bytes(content)
    return dest


async def probe_cert(
    cert_number: str,
    boat_name: str,
    sail_number: str,
    year: int | None = None,
) -> dict:
    """Try, in order:

    1. Live IRC PDF directory for each candidate URL.
    2. Wayback snapshot for each candidate URL.

    Returns ``{source, pdf_path, status}``. ``source`` is one of ``"live"``,
    ``"wayback"``, or ``None``; ``status`` is ``"found"`` or ``"not_found"``.
    """
    candidates = build_cert_url_variants(cert_number, boat_name, sail_number)

    async with get_http_client() as client:
        # Strategy 1: live HEAD probe, then GET on hit.
        for url in candidates:
            await head_limiter.wait()
            try:
                head = await client.head(url, follow_redirects=True)
            except Exception:
                continue
            if head.status_code != 200:
                continue
            ct = head.headers.get("content-type", "").lower()
            cl = int(head.headers.get("content-length", "0") or 0)
            if "pdf" not in ct and cl < 1000:
                continue
            await download_limiter.wait()
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception:
                continue
            if resp.content[:5] != b"%PDF-":
                continue
            path = _save_pdf(resp.content, cert_number)
            return {"source": "live", "pdf_path": path, "status": "found"}

        # Strategy 2: Wayback fallback.
        for url in candidates:
            archived = await lookup_pdf_in_wayback(url, client=client)
            if archived is None:
                continue
            path = _save_pdf(
                archived["content"], cert_number, prefix="wayback"
            )
            return {"source": "wayback", "pdf_path": path, "status": "found"}

    return {"source": None, "pdf_path": None, "status": "not_found"}


async def backfill_from_index(
    index: list[dict],
    *,
    resume: bool = True,
    progress_every: int = 25,
) -> dict:
    """Probe every entry in ``index``. Persist progress to state file.

    ``index`` rows are passed through to :func:`probe_cert` as kwargs, so
    they must contain ``cert_number``, ``boat_name``, ``sail_number`` (and
    optionally ``year``). Extra keys are ignored.
    """
    state = _load_state() if resume else {"done": []}
    done: set[str] = set(state.get("done", []))
    stats = {"found_live": 0, "found_wayback": 0, "not_found": 0}

    for i, entry in enumerate(index, start=1):
        cert_no = entry.get("cert_number")
        if not cert_no:
            continue
        if cert_no in done:
            continue
        kwargs = {
            "cert_number": cert_no,
            "boat_name": entry.get("boat_name", ""),
            "sail_number": entry.get("sail_number", ""),
            "year": entry.get("year"),
        }
        try:
            result = await probe_cert(**kwargs)
        except Exception as exc:
            print(f"  probe_cert({cert_no}) raised {exc}; treating as not_found")
            result = {"source": None, "status": "not_found"}
        if result["status"] == "found":
            stats[f"found_{result['source']}"] += 1
        else:
            stats["not_found"] += 1
        done.add(cert_no)
        state["done"] = sorted(done)
        _save_state(state)
        if i % progress_every == 0:
            total = stats["found_live"] + stats["found_wayback"]
            print(
                f"  Progress: {i}/{len(index)} probed, {total} found "
                f"(live={stats['found_live']}, wayback={stats['found_wayback']})"
            )

    return stats
