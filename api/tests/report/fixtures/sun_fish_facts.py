"""Known-good golden values for SUN FISH (boat_id 12330).

These values are stable post-dedup (2026-05-20). If a test asserting
them fails, EITHER the underlying data has shifted (re-snapshot
intentionally) OR the builder has regressed (fix the builder).
"""
SUN_FISH_BOAT_ID = 12330
SUN_FISH_DESIGN = "Sunfast 3300"
SUN_FISH_TCC_LOWER = 1.02
SUN_FISH_TCC_UPPER = 1.03   # current rating sits inside this band
