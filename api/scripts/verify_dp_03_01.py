#!/usr/bin/env python3
"""End-to-end verification evidence for DP-03-01 — canonical entity
boundaries and identifiers.

Walks the five messy real-world examples of
``docs/architecture/canonical-entities.md`` §6 through the domain model
and prints hard, paste-able PASS/FAIL evidence for the issue board.

No database or network required — the model under test is the pure
in-memory canonical registry in ``irc_data.domain`` layered on the
DP-03-02 bitemporal assertion store.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_03_01.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.assertions import AssertionV1  # noqa: E402
from irc_data.domain import (  # noqa: E402
    Alias,
    DomainModel,
    EntityType,
    check_id_opacity,
    entity_types,
    new_entity_id,
    parse_entity_id,
)

UTC = timezone.utc
RESULTS: list[tuple[str, bool, str]] = []


def T(y: int, m: int = 1, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def boat_tcc(model, boat, value, source, recorded, **kw):
    a = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="tcc",
                    value=value, source_slug=source, recorded_at=recorded,
                    valid_from=kw.get("valid_from", recorded), valid_to=kw.get("valid_to"))
    model.assert_about(boat.entity_id, a)
    return a


print("=" * 76)
print("DP-03-01 verification — canonical entity boundaries and identifiers")
print("=" * 76)

# ---------------------------------------------------------------------------
print("\n0. Identifier opacity (acceptance criterion 2)")
oid = new_entity_id(EntityType.BOAT)
parts = parse_entity_id(oid)
check("ids are <prefix>_<26-char ULID>", parts.entity_type is EntityType.BOAT and len(parts.ulid) == 26, oid)
try:
    check_id_opacity("boat_GBR8310AAAAAAAAAAAAAAAAAAA")
    check("name-derived id rejected", False, "GBR sail-number body accepted!")
except Exception as exc:
    check("name-derived id rejected", type(exc).__name__ == "IdentifierDerivationError", type(exc).__name__)
check("15 canonical types registered", len(entity_types()) == 15, ", ".join(t.value for t in entity_types()))

# ---------------------------------------------------------------------------
print("\n1. Renamed boat (Wild Thing -> Wild Oats XI): one hull, one id")
m = DomainModel()
boat = m.create_entity("boat", at=T(2002, 6))
i1 = m.create_entity("boat_identity", at=T(2002, 6))
i2 = m.create_entity("boat_identity", at=T(2004, 6))
m.attach_alias(i1.entity_id, Alias(kind="boat_name", value="Wild Thing", valid_from=T(2002), valid_to=T(2004)))
m.attach_alias(i2.entity_id, Alias(kind="boat_name", value="Wild Oats XI", valid_from=T(2004)))
boat_tcc(m, boat, 1.550, "irc-certs", T(2003, 5), valid_to=T(2004, 5))
boat_tcc(m, boat, 1.612, "irc-certs", T(2005, 5))
check("boat id stable across rename",
      m.resolve_truth(boat.entity_id, as_of=T(2003, 8)).entity_id == boat.entity_id
      == m.resolve_truth(boat.entity_id, as_of=T(2005, 8)).entity_id)
check("2003 view resolves era-1 rating", m.resolve_truth(boat.entity_id, as_of=T(2003, 8)).value("tcc") == 1.550)
check("2005 view resolves era-2 rating", m.resolve_truth(boat.entity_id, as_of=T(2005, 8)).value("tcc") == 1.612)
check("time-scoped name lookup", m.resolve_alias("boat_name", "Wild Thing", at=T(2003)).entity_id == i1.entity_id
      and m.resolve_alias("boat_name", "Wild Oats XI", at=T(2005)).entity_id == i2.entity_id)

# ---------------------------------------------------------------------------
print("\n2. Re-issued sail number (GBR 8310 on two hulls): corrective split")
m = DomainModel()
era1 = m.create_entity("boat", at=T(2008))
m.attach_alias(era1.entity_id, Alias(kind="sail_number", value="GBR 8310", valid_from=T(2008), valid_to=T(2010)))
boat_tcc(m, era1, 1.011, "sailsys", T(2008, 6))
bad = boat_tcc(m, era1, 1.204, "irc-certs", T(2019, 6))  # mis-attached by ingestion
era2 = m.split(era1.entity_id, assertion_ids=[bad.assertion_id], at=T(2019, 7),
               reason="sail number re-issued to a new hull")
m.attach_alias(era2.entity_id, Alias(kind="sail_number", value="GBR 8310", valid_from=T(2019, 7)))
check("2009 lookup -> hull 1", m.resolve_alias("sail_number", "GBR8310", at=T(2009)).entity_id == era1.entity_id)
check("2020 lookup -> hull 2", m.resolve_alias("sail_number", "GBR8310", at=T(2020)).entity_id == era2.entity_id)
check("post-split truths separated",
      m.resolve_truth(era1.entity_id, as_of=T(2020)).value("tcc") == 1.011
      and m.resolve_truth(era2.entity_id, as_of=T(2020)).value("tcc") == 1.204)
check("split provenance recorded", era2.split_from == era1.entity_id)

# ---------------------------------------------------------------------------
print("\n3. Duplicate design (Sydney 38 vs Sydney 38 OD): auditable merge")
m = DomainModel()
d1 = m.create_entity("design", at=T(2000))
d2 = m.create_entity("design", at=T(2001))
boat = m.create_entity("boat", at=T(2002))
a1 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="design",
                 value=str(d1.entity_id), source_slug="irc-certs",
                 recorded_at=T(2003), valid_from=T(2003), confidence=0.9)
a2 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="design",
                 value=str(d2.entity_id), source_slug="sailsys",
                 recorded_at=T(2004), valid_from=T(2004), confidence=0.6)
m.assert_about(boat.entity_id, a1)
m.assert_about(boat.entity_id, a2)
pre = m.resolve_truth(boat.entity_id, as_of=T(2005))
check("pre-merge conflict observable", bool(pre.conflicts.get("design")))
m.merge(d1.entity_id, d2.entity_id, at=T(2006), reason="same class")
a3 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="design",
                 value=str(d1.entity_id), source_slug="sailsys",
                 recorded_at=T(2006, 2), valid_from=T(2006, 2), supersedes=a2.assertion_id)
m.assert_about(boat.entity_id, a3)
m._assertions[a2.assertion_id] = AssertionV1.from_dict(
    {**a2.to_dict(), "superseded_by": a3.assertion_id, "superseded_at": T(2006, 2).isoformat()})
post = m.resolve_truth(boat.entity_id, as_of=T(2006, 3))
check("post-merge truth is the survivor design", post.value("design") == str(d1.entity_id))
check("removed design preserved (merged_into)", m._entities[d2.entity_id].merged_into == d1.entity_id)
check("pre-merge state still reproducible",
      bool(m.resolve_truth(boat.entity_id, as_of=T(2005)).conflicts.get("design")))

# ---------------------------------------------------------------------------
print("\n4. Corrected race result (DNF -> 3rd on redress): both states reproduce")
m = DomainModel()
event = m.create_entity("event", at=T(2019, 12, 1))
race = m.create_entity("race", at=T(2019, 12, 26))
entry = m.create_entity("entry", at=T(2019, 12, 1))
result = m.create_entity("result", at=T(2019, 12, 28))
t1, t2 = T(2019, 12, 29), T(2020, 1, 15)
a1 = AssertionV1(entity_type="result", entity_key=result.entity_key, field="status",
                 value="DNF", source_slug="sailsys", recorded_at=t1, valid_from=t1)
m.assert_about(result.entity_id, a1)
a2 = AssertionV1(entity_type="result", entity_key=result.entity_key, field="status",
                 value="finished", source_slug="sailsys", recorded_at=t2, valid_from=t1,
                 supersedes=a1.assertion_id)
m.assert_about(result.entity_id, a2)
m._assertions[a1.assertion_id] = AssertionV1.from_dict(
    {**a1.to_dict(), "superseded_by": a2.assertion_id, "superseded_at": t2.isoformat()})
a3 = AssertionV1(entity_type="result", entity_key=result.entity_key, field="place",
                 value=3, source_slug="sailsys", recorded_at=t2, valid_from=t1)
m.assert_about(result.entity_id, a3)
check("as published (T1): DNF", m.resolve_truth(result.entity_id, as_of=T(2020, 1, 1)).value("status") == "DNF")
now = m.resolve_truth(result.entity_id, as_of=T(2020, 2))
check("as corrected (T2): finished 3rd", now.value("status") == "finished" and now.value("place") == 3)
check("event/race/entry boundaries untouched",
      event.entity_type is EntityType.EVENT and race.entity_type is EntityType.RACE
      and entry.entity_type is EntityType.ENTRY)

# ---------------------------------------------------------------------------
print("\n5. Venue renamed + organiser change (Hamilton Island): venue id stable")
m = DomainModel()
venue = m.create_entity("venue", at=T(2004, 8))
m.attach_alias(venue.entity_id, Alias(kind="venue_name", value="Hamilton Island",
                                      valid_from=T(2004), valid_to=T(2016)))
m.attach_alias(venue.entity_id, Alias(kind="venue_name", value="Hamilton Island Marina",
                                      valid_from=T(2016)))
org_old = m.create_entity("organisation", at=T(2004))
org_new = m.create_entity("organisation", at=T(2018))
ev15 = m.create_entity("event", at=T(2015, 8))
ev19 = m.create_entity("event", at=T(2019, 8))
for ev, org, when in ((ev15, org_old, T(2015, 8)), (ev19, org_new, T(2019, 8))):
    m.assert_about(ev.entity_id, AssertionV1(
        entity_type="event", entity_key=ev.entity_key, field="organiser",
        value=str(org.entity_id), source_slug="yachtscoring", recorded_at=when, valid_from=when))
    m.assert_about(ev.entity_id, AssertionV1(
        entity_type="event", entity_key=ev.entity_key, field="venue",
        value=str(venue.entity_id), source_slug="yachtscoring", recorded_at=when, valid_from=when))
check("venue name resolves per era to ONE venue id",
      m.resolve_alias("venue_name", "Hamilton Island", at=T(2010)).entity_id == venue.entity_id
      == m.resolve_alias("venue_name", "Hamilton Island Marina", at=T(2020)).entity_id)
t15 = m.resolve_truth(ev15.entity_id, as_of=T(2016))
t19 = m.resolve_truth(ev19.entity_id, as_of=T(2020))
check("both editions reference the same venue id",
      t15.value("venue") == t19.value("venue") == str(venue.entity_id))
check("organiser handover honest (two orgs)",
      t15.value("organiser") == str(org_old.entity_id) and t19.value("organiser") == str(org_new.entity_id))

# ---------------------------------------------------------------------------
print("\n" + "=" * 76)
n_pass = sum(1 for _, ok, _ in RESULTS if ok)
n_fail = len(RESULTS) - n_pass
print(f"RESULT: {n_pass} passed, {n_fail} failed, {len(RESULTS)} checks")
print("=" * 76)
sys.exit(1 if n_fail else 0)
