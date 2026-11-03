"""Sailwave race results printed to PDF -> Sailwave-shaped HTML.

Some clubs (Warsash 2022-2024) post "print to PDF" copies of Sailwave pages
instead of the HTML. When the PDF has a text layer, each race block keeps
Sailwave's layout: event / venue / "Results are provisional as of ..." lines,
a race title ("R1 - IRC 1 Champs - April 20 at 10.45"), a header row and one
line per boat (long cells wrap onto a continuation line). Words are assigned
to columns by the header words' x positions, and the result is rendered as
the HTML structure :func:`irc_data.scrapers.sailwave.parse_sailwave_html`
already reads, so IRC classification and row rules are shared.

Only race blocks are converted; printed series summaries (multi-line,
wrapped column headers) are not. Image-only PDFs yield nothing.
"""

from __future__ import annotations

import html as _html
import io
import re

_RACE_TITLE_RE = re.compile(r"^(R|Race)\s*\d+\s+-\s+\S", re.IGNORECASE)
_RANK_RE = re.compile(r"^\d+(st|nd|rd|th)?$|^(DNC|DNF|DNS|RET|DSQ|OCS|BFD|UFD|NSC)$", re.I)


def _lines(page) -> list[list[dict]]:
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= 2.5:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _text(line: list[dict]) -> str:
    return " ".join(w["text"] for w in line)


def pdf_to_sailwave_html(data: bytes) -> str | None:
    """HTML for the race blocks of a Sailwave PDF, or None (no text / not Sailwave)."""
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception:  # noqa: BLE001 - not a readable PDF
        return None
    with pdf:
        title = (pdf.metadata or {}).get("Title") or ""
        lines: list[list[dict]] = []
        for page in pdf.pages:
            lines.extend(_lines(page))
    if not lines:
        return None
    texts = [_text(l) for l in lines]
    if "sailwave" not in title.lower() and not any("Sailwave" in t for t in texts):
        return None

    out = ["<html><head><title>" + _html.escape(title) + "</title></head><body>"]
    i = 0
    # Header: event, venue, as-of line (first page).
    as_of_idx = next((k for k, t in enumerate(texts[:8]) if t.startswith("Results are")), None)
    if as_of_idx is not None and as_of_idx >= 2:
        out.append(f"<h1>{_html.escape(' '.join(texts[:as_of_idx - 1]))}</h1>")
        out.append(f"<h2>{_html.escape(texts[as_of_idx - 1])}</h2>")
        out.append(f'<h3 class="seriestitle">{_html.escape(texts[as_of_idx])}</h3>')
        i = as_of_idx + 1
    blocks = 0
    while i < len(lines):
        if not _RACE_TITLE_RE.match(texts[i]):
            i += 1
            continue
        race_title = texts[i]
        # Find the header row ("Rank ...") within the next few lines.
        j = i + 1
        while j < len(lines) and j <= i + 3 and not texts[j].startswith("Rank"):
            j += 1
        if j >= len(lines) or not texts[j].startswith("Rank"):
            i += 1
            continue
        header = lines[j]
        starts = [w["x0"] for w in header]
        names = [w["text"] for w in header]
        rows: list[list[str]] = []
        k = j + 1
        while k < len(lines):
            t = texts[k]
            if _RACE_TITLE_RE.match(t) or t.startswith(("Results are", "Sailwave", "Scoring codes")) \
                    or re.match(r"^(Start:|Code Description|Sailed:)", t):
                break
            cells = [""] * len(names)
            for w in lines[k]:
                col = max((c for c, x in enumerate(starts) if x <= w["x0"] + 3), default=0)
                cells[col] = (cells[col] + " " + w["text"]).strip()
            if _RANK_RE.match(cells[0] or "") or (cells[0] and cells[0][0].isdigit()):
                rows.append(cells)
            elif rows:  # wrapped continuation of the previous boat's cells
                for c, v in enumerate(cells):
                    if v:
                        rows[-1][c] = (rows[-1][c] + " " + v).strip()
            k += 1
        out.append(f'<h3 class="racetitle">{_html.escape(race_title)}</h3>')
        out.append('<table class="racetable"><thead><tr>'
                   + "".join(f"<th>{_html.escape(n)}</th>" for n in names)
                   + "</tr></thead><tbody>")
        for r in rows:
            out.append("<tr>" + "".join(f"<td>{_html.escape(v)}</td>" for v in r) + "</tr>")
        out.append("</tbody></table>")
        blocks += 1
        i = k
    out.append("<p>Sailwave Scoring Software (PDF)</p></body></html>")
    return "\n".join(out) if blocks else None
