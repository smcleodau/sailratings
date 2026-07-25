"""Legacy Claude client forwarding wrapper.

All calls are routed transparently to gemini_client.
"""
from irc_data.api.services.report.gemini_client import (
    extract_numeric_tokens,
    facts_numeric_allowlist,
    audit_section_numbers,
    call_gemini,
    call_claude,
    _normalise_number,
)
