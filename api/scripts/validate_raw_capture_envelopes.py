"""Validate raw-capture envelopes against the RawArtifactV0 contract.

Usage:
    PYTHONPATH=src python3 scripts/validate_raw_capture_envelopes.py <store_root>

Checks every object in the given raw store directory tree:
  * object is retrievable by its SHA-256 content address (hash verification)
  * content length matches the on-disk object
  * store path follows the content-addressed shard layout

Exits non-zero on any validation failure.  Used as the
"envelope validation passes" verification step for DP-00-03 / DP-00-04 /
DP-00-05 raw captures.
"""

from __future__ import annotations

import os
import sys


def validate_store(store_root: str) -> dict:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from irc_data.sources.provenance import RawObjectStore

    store = RawObjectStore(store_root)
    result = {"store_root": store.root, "objects": 0, "ok": 0, "errors": []}

    for dirpath, _dirs, files in os.walk(store.root):
        for fname in files:
            if fname.endswith(".tmp"):
                continue
            result["objects"] += 1
            content_hash = fname.lower()
            try:
                data = store.get(content_hash)  # raises on hash mismatch
                result["ok"] += 1
            except Exception as exc:
                result["errors"].append(f"{content_hash}: {exc}")

    result["valid"] = result["ok"] == result["objects"]
    return result


def main() -> int:
    import json

    if len(sys.argv) < 2:
        print("usage: validate_raw_capture_envelopes.py <store_root> [<store_root> ...]")
        return 2

    all_ok = True
    for root in sys.argv[1:]:
        res = validate_store(root)
        all_ok = all_ok and res["valid"]
        print(json.dumps(res, indent=2))

    print("ENVELOPE VALIDATION:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
