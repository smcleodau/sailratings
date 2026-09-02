"""Representative TopYacht source fixtures (DP-06-02).

This package preserves **representative source variants and errors** so the
certified adapter + parser can be tested with **zero network calls**.

Fixture families
----------------

* **Index pages** — ``{year}/{division}/index.htm`` listing series.
* **Series pages** — ``series.htm`` with the IRC column table.
* **Race-result variants** — the TopYacht template varies across clubs /
  seasons; we preserve the representative variants:
    - ``standard``     — canonical ``centre_results_table`` with ``Place /
      Sail No / Boat Name / Skipper / AHC / Cor'd T`` headers and an IRC
      caption.
    - ``dnf``          — a race with DNF/RET/DNS status codes in the Place
      column (status detection).
    - ``multiclass``   — a page with multiple division tables (only the
      IRC-captioned tables must be parsed).
    - ``no_irc``       — a page whose only table is PHS (parser must emit
      zero records).
* **Errors / breakage mutations** — used by the mutation tests to prove
  the parser *detects* source breakage rather than silently emitting
  garbage:
    - ``mutated_no_tables``      — results table removed entirely.
    - ``mutated_headers_renamed``— column headers renamed (boat column lost).
    - ``mutated_irrelevant``     — an unrelated page (no result content).

Each fixture is raw HTML ``bytes`` served by a
:class:`~irc_data.sources.fake_adapter.FakeHttpServer` in the tests.
"""

from __future__ import annotations

FIXTURE_SLUG = "topyacht"
