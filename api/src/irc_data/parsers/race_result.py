"""Race result HTML parser — re-exports from race_results scraper."""

# The actual parsing logic is in scrapers/race_results.py (parse_cyca_result_html)
# This module exists for the planned structure and can house additional parsers.

from irc_data.scrapers.race_results import parse_cyca_result_html

__all__ = ["parse_cyca_result_html"]
