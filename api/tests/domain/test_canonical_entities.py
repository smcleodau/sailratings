"""Contract and domain-review tests for canonical entities (DP-03-01).

Two halves:

1. **Contract tests** — identifier opacity, boundary registry integrity,
   the assertion/resolved-truth separation, bitemporal reproducibility,
   alias semantics, and merge/split lifecycle.
2. **Domain review** — the five messy real-world examples of
   ``docs/architecture/canonical-entities.md`` §6, executed end-to-end
   against the model with the stated outcomes asserted.  These are the
   acceptance-criteria verification ("at least five messy real-world
   examples walked through the model").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from irc_data.assertions import AssertionV1
from irc_data.domain import (
    ENTITY_BOUNDARIES,
    ID_PREFIXES,
    PREFIX_TO_TYPE,
    Alias,
    AliasedToRemovedError,
    AliasInconsistentError,
    DomainError,
    DomainModel,
    DuplicateAliasError,
    EntityNotFoundError,
    EntityType,
    IdentifierDerivationError,
    MergeSameEntityError,
    SplitError,
    check_id_opacity,
    entity_boundary,
    entity_types,
    new_entity_id,
    parse_entity_id,
)

UTC = timezone.utc
DAY = timedelta(days=1)


def T(y: int, m: int = 1, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def assert_boat(model: DomainModel, boat, field_name: str, value, *, source: str,
                recorded: datetime, valid_from: datetime | None = None,
                valid_to: datetime | None = None, supersedes: str | None = None):
    a = AssertionV1(
        entity_type="boat",
        entity_key=boat.entity_key,
        field=field_name,
        value=value,
        source_slug=source,
        recorded_at=recorded,
        valid_from=valid_from or recorded,
        valid_to=valid_to,
        supersedes=supersedes,
    )
    return model.assert_about(boat.entity_id, a)


# ---------------------------------------------------------------------------
# 1. Identifier contract
# ---------------------------------------------------------------------------


class TestIdentifiers:
    def test_every_entity_type_has_a_prefix_and_back(self):
        for et in EntityType:
            assert et in ID_PREFIXES
            assert PREFIX_TO_TYPE[ID_PREFIXES[et]] is et

    def test_new_id_shape_and_roundtrip(self):
        oid = new_entity_id(EntityType.BOAT)
        parts = parse_entity_id(oid)
        assert parts.entity_type is EntityType.BOAT
        assert str(parts) == oid
        assert len(parts.ulid) == 26

    def test_ids_are_unique_and_sortable_by_creation_time(self):
        a = new_entity_id("boat", _time_ms=1_000)
        b = new_entity_id("boat", _time_ms=2_000)
        assert a != b
        assert a < b  # ULID bodies are lexicographically time-sortable

    def test_id_contains_no_mutable_name_material(self):
        # Even the *pin the entropy* path can only produce Crockford chars —
        # no lowercase, no I/L/O/U, and no way to inject a name.
        oid = new_entity_id("boat", _time_ms=1_726_000_000_000)
        body = parse_entity_id(oid).ulid
        assert body.isalnum() and body.upper() == body
        for banned in "ILOU":
            assert banned not in body

    def test_parse_rejects_garbage_and_unknown_prefix(self):
        with pytest.raises(DomainError):
            parse_entity_id("not-an-id")
        with pytest.raises(DomainError):
            parse_entity_id("gizmo_01J4Z9K7W2Q8E0R1T3Y5U7I9O0")

    def test_opacity_audit_passes_clean_ids(self):
        oid = new_entity_id("boat")
        check_id_opacity(oid, aliases=["Wild Oats", "GBR 8310"])  # no raise

    def test_opacity_audit_rejects_name_derived_keys(self):
        with pytest.raises(IdentifierDerivationError):
            check_id_opacity("boat_GBR8310AAAAAAAAAAAAAAAAAAA")  # 26-char body, GBR prefix
        with pytest.raises(IdentifierDerivationError):
            check_id_opacity(
                "boat_AUS8310BBBBBBBBBBBBBBBBBBB",  # country prefix with a U
            )
        with pytest.raises(IdentifierDerivationError):
            check_id_opacity(
                "boat_W1LDTHINGAAAAAAAAAAAAAAAAA",  # 26-char body from a boat name
                aliases=["Wild Thing"],
            )

    def test_opacity_audit_body_lengths_are_enforced(self):
        # 26-char ULID body: shorter or longer is malformed, not opaque.
        with pytest.raises(DomainError):
            check_id_opacity("boat_GBR8310AAAAAAAAAAAAAAAAAA")  # 27 chars
        with pytest.raises(DomainError):
            check_id_opacity("boat_0123")

    def test_opacity_audit_rejects_malformed(self):
        with pytest.raises(DomainError):
            check_id_opacity("GBR 8310")


# ---------------------------------------------------------------------------
# 2. Boundary registry
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_fifteen_canonical_types_in_scope_order(self):
        assert [t.value for t in entity_types()] == [
            "boat", "boat_identity", "design", "organisation", "person",
            "event", "race", "entry", "result", "certificate", "rating",
            "measurement", "sail", "venue", "source_assertion",
        ]

    def test_every_type_has_a_documented_boundary(self):
        for et in EntityType:
            b = entity_boundary(et)
            assert b.definition
            assert b.aliases, f"{et} must declare its mutable-name aliases"
            assert b.contains, f"{et} must declare what it contains"
            assert "name" not in " ".join(b.contains).lower() or \
                et is EntityType.SOURCE_ASSERTION, (
                    f"{et} must not *contain* a mutable name as identity"
                )

    def test_boundaries_are_the_data(self):
        assert ENTITY_BOUNDARIES[EntityType.CERTIFICATE].references == (
            EntityType.BOAT, EntityType.ORGANISATION,
        )


# ---------------------------------------------------------------------------
# 3. Assertion / resolved-truth separation + temporal history
# ---------------------------------------------------------------------------


class TestTruthSeparation:
    def test_entity_shell_carries_no_fact_values(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        # The shell has no tcc/name/design attributes at all.
        for forbidden in ("tcc", "rating", "boat_name", "sail_number", "design"):
            assert not hasattr(boat, forbidden)

    def test_resolved_truth_derives_from_assertions_only(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        assert model.resolve_truth(boat.entity_id, as_of=T(2024, 2)).value("tcc") is None
        assert_boat(model, boat, "tcc", 1.023, source="irc-certs", recorded=T(2024, 1, 2))
        truth = model.resolve_truth(boat.entity_id, as_of=T(2024, 2))
        assert truth.value("tcc") == 1.023

    def test_assertion_must_reference_the_entitys_opaque_id(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        bad = AssertionV1(
            entity_type="boat", entity_key="GBR 8310", field="tcc", value=1.0,
            source_slug="sailsys", recorded_at=T(2024), valid_from=T(2024),
        )
        with pytest.raises(AliasInconsistentError):
            model.assert_about(boat.entity_id, bad)
        wrong_type = AssertionV1(
            entity_type="certificate", entity_key=boat.entity_key, field="tcc",
            value=1.0, source_slug="sailsys", recorded_at=T(2024), valid_from=T(2024),
        )
        with pytest.raises(AliasInconsistentError):
            model.assert_about(boat.entity_id, wrong_type)

    def test_resolution_is_reproducible_for_prior_system_time(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        a1 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="tcc",
                         value=1.011, source_slug="sailsys",
                         recorded_at=T(2024, 3, 1), valid_from=T(2024, 3, 1))
        model.assert_about(boat.entity_id, a1)
        a2 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="tcc",
                         value=1.017, source_slug="irc-certs",
                         recorded_at=T(2024, 6, 1), valid_from=T(2024, 6, 1),
                         supersedes=a1.assertion_id)
        model.assert_about(boat.entity_id, a2)
        # As of April we only knew the first value; as of July the correction.
        assert model.resolve_truth(boat.entity_id, as_of=T(2024, 4, 1)).value("tcc") == 1.011
        assert model.resolve_truth(boat.entity_id, as_of=T(2024, 7, 1)).value("tcc") == 1.017
        # Re-running the same as_of always yields the same winner.
        again = model.resolve_truth(boat.entity_id, as_of=T(2024, 4, 1))
        assert again.fields["tcc"].assertion_id == a1.assertion_id


# ---------------------------------------------------------------------------
# 4. Aliases
# ---------------------------------------------------------------------------


class TestAliases:
    def test_alias_resolves_case_and_whitespace_insensitively(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        model.attach_alias(boat.entity_id, Alias(
            kind="sail_number", value="GBR 8310", valid_from=T(2024),
            source_slug="irc-certs"))
        assert model.resolve_alias("sail_number", "  gbr  8310 ") is not None

    def test_overlapping_alias_on_two_live_entities_rejected(self):
        model = DomainModel()
        b1 = model.create_entity("boat", at=T(2024))
        b2 = model.create_entity("boat", at=T(2024))
        model.attach_alias(b1.entity_id, Alias(kind="sail_number", value="K 1",
                                               valid_from=T(2024)))
        with pytest.raises(DuplicateAliasError):
            model.attach_alias(b2.entity_id, Alias(kind="sail_number", value="K 1",
                                                   valid_from=T(2024)))

    def test_disjoint_alias_intervals_coexist(self):
        model = DomainModel()
        b1 = model.create_entity("boat", at=T(2008))
        b2 = model.create_entity("boat", at=T(2019))
        model.attach_alias(b1.entity_id, Alias(kind="sail_number", value="GBR8310",
                                               valid_from=T(2008), valid_to=T(2010)))
        model.attach_alias(b2.entity_id, Alias(kind="sail_number", value="GBR8310",
                                               valid_from=T(2019)))
        assert model.resolve_alias("sail_number", "GBR8310", at=T(2009)).entity_id == b1.entity_id
        assert model.resolve_alias("sail_number", "GBR8310", at=T(2020)).entity_id == b2.entity_id

    def test_cannot_alias_a_removed_entity(self):
        model = DomainModel()
        b1 = model.create_entity("boat", at=T(2024))
        b2 = model.create_entity("boat", at=T(2024))
        model.merge(b1.entity_id, b2.entity_id, at=T(2024, 2))
        with pytest.raises(AliasedToRemovedError):
            model.attach_alias(b2.entity_id, Alias(kind="boat_name", value="X",
                                                   valid_from=T(2024, 3)))


# ---------------------------------------------------------------------------
# 5. Lifecycle: merge, split, history
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_merge_rejects_self_and_cross_type(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        cert = model.create_entity("certificate", at=T(2024))
        with pytest.raises(MergeSameEntityError):
            model.merge(boat.entity_id, boat.entity_id)
        with pytest.raises(DomainError):
            model.merge(boat.entity_id, cert.entity_id)

    def test_merge_preserves_history_and_repoints_assertions(self):
        model = DomainModel()
        keep = model.create_entity("boat", at=T(2024))
        gone = model.create_entity("boat", at=T(2024))
        assert_boat(model, gone, "design", "Sydney 38", source="sailsys", recorded=T(2024, 1, 5))
        model.merge(keep.entity_id, gone.entity_id, at=T(2024, 2), reason="same hull")
        removed = model._entities[gone.entity_id]
        assert removed.merged_into == keep.entity_id
        assert removed.removed_at == T(2024, 2)
        truth = model.resolve_truth(keep.entity_id, as_of=T(2024, 3))
        assert truth.value("design") == "Sydney 38"
        # The merge itself is in the append-only registry log.
        kinds = [e.event_type for e in model.event_log]
        assert kinds == ["create", "create", "merge"]

    def test_get_respects_liveness_window(self):
        model = DomainModel()
        b1 = model.create_entity("boat", at=T(2024))
        b2 = model.create_entity("boat", at=T(2024))
        model.merge(b1.entity_id, b2.entity_id, at=T(2024, 2))
        assert model.get(b2.entity_id, at=T(2024, 1, 15)).entity_id == b2.entity_id
        with pytest.raises(EntityNotFoundError):
            model.get(b2.entity_id, at=T(2024, 3))

    def test_split_moves_only_named_assertions(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2008))
        assert_boat(model, boat, "tcc", 1.011, source="sailsys", recorded=T(2008, 6))
        r19 = assert_boat(model, boat, "tcc", 1.204, source="irc-certs", recorded=T(2019, 6))
        new = model.split(boat.entity_id, assertion_ids=[r19.assertion_id], at=T(2020, 1))
        assert new.split_from == boat.entity_id
        assert model.resolve_truth(boat.entity_id, as_of=T(2020, 2)).value("tcc") == 1.011
        assert model.resolve_truth(new.entity_id, as_of=T(2020, 2)).value("tcc") == 1.204

    def test_split_rejects_foreign_assertions(self):
        model = DomainModel()
        b1 = model.create_entity("boat", at=T(2024))
        b2 = model.create_entity("boat", at=T(2024))
        ref = assert_boat(model, b2, "tcc", 1.0, source="sailsys", recorded=T(2024, 2))
        with pytest.raises(SplitError):
            model.split(b1.entity_id, assertion_ids=[ref.assertion_id], at=T(2024, 3))

    def test_event_log_is_append_only_and_time_ordered(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        model.attach_alias(boat.entity_id, Alias(kind="boat_name", value="AKELA",
                                                 valid_from=T(2024, 1, 2)))
        seqs = [e.seq for e in model.event_log]
        assert seqs == list(range(1, len(seqs) + 1))
        assert [e.event_type for e in model.registry_events_since(T(2024, 1, 2))] == ["alias_attach"]

    def test_snapshot_serialises_entities_and_log(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2024))
        snap = model.snapshot()
        assert snap["schema_version"] == "canonical-entity-v1"
        assert snap["entities"][0]["entity_id"] == boat.entity_id
        assert snap["event_log"][0]["event_type"] == "create"


# ---------------------------------------------------------------------------
# 6. Domain review — five messy real-world examples (verification criterion)
# ---------------------------------------------------------------------------


class TestWalkthrough1RenamedBoat:
    """6.1 — 'Wild Thing' renamed to 'Wild Oats XI' and back."""

    def test_rename_keeps_one_boat_one_id(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2002, 6))
        ident1 = model.create_entity("boat_identity", at=T(2002, 6))
        ident2 = model.create_entity("boat_identity", at=T(2004, 6))
        model.attach_alias(ident1.entity_id, Alias(kind="boat_name", value="Wild Thing",
                                                   valid_from=T(2002), valid_to=T(2004)))
        model.attach_alias(ident2.entity_id, Alias(kind="boat_name", value="Wild Oats XI",
                                                   valid_from=T(2004)))
        # Ratings across both eras assert about the SAME opaque boat id.
        assert_boat(model, boat, "tcc", 1.550, source="irc-certs",
                    recorded=T(2003, 5), valid_from=T(2003, 5), valid_to=T(2004, 5))
        assert_boat(model, boat, "tcc", 1.612, source="irc-certs",
                    recorded=T(2005, 5), valid_from=T(2005, 5))
        early = model.resolve_truth(boat.entity_id, as_of=T(2003, 8))
        late = model.resolve_truth(boat.entity_id, as_of=T(2005, 8))
        assert early.entity_id == late.entity_id == boat.entity_id
        assert early.value("tcc") == 1.550
        assert late.value("tcc") == 1.612
        # Alias lookup is time-correct; the id never moved.
        assert model.resolve_alias("boat_name", "Wild Thing", at=T(2003)).entity_id == ident1.entity_id
        assert model.resolve_alias("boat_name", "Wild Oats XI", at=T(2005)).entity_id == ident2.entity_id


class TestWalkthrough2ReissuedSailNumber:
    """6.2 — GBR 8310 names two different hulls in different eras."""

    def test_reissued_sail_number_and_corrective_split(self):
        model = DomainModel()
        era1 = model.create_entity("boat", at=T(2008, 1))
        model.attach_alias(era1.entity_id, Alias(kind="sail_number", value="GBR 8310",
                                                 valid_from=T(2008), valid_to=T(2010)))
        assert_boat(model, era1, "tcc", 1.011, source="sailsys", recorded=T(2008, 6))
        # Ingestion error: the 2019 certificate assertion lands on the old boat.
        bad = assert_boat(model, era1, "tcc", 1.204, source="irc-certs", recorded=T(2019, 6))
        # Domain review: two hulls.  Split the 2019 assertion onto a new boat.
        era2 = model.split(era1.entity_id, assertion_ids=[bad.assertion_id], at=T(2019, 7),
                           reason="sail number re-issued to a new hull")
        model.attach_alias(era2.entity_id, Alias(kind="sail_number", value="GBR 8310",
                                                 valid_from=T(2019, 7)))
        # Inside each alias's validity the label resolves to the right hull;
        # the pre-split state is reconstructable via system-time filtering.
        assert model.resolve_alias("sail_number", "GBR8310", at=T(2009)).entity_id == era1.entity_id
        assert model.resolve_alias("sail_number", "GBR8310", at=T(2020)).entity_id == era2.entity_id
        assert model.resolve_truth(era1.entity_id, as_of=T(2020)).value("tcc") == 1.011
        assert model.resolve_truth(era2.entity_id, as_of=T(2020)).value("tcc") == 1.204
        assert era2.split_from == era1.entity_id


class TestWalkthrough3DuplicateDesignMerge:
    """6.3 — 'Sydney 38' vs 'Sydney 38 OD' entered as two designs."""

    def test_duplicate_design_merge_clears_conflicts_but_keeps_history(self):
        model = DomainModel()
        d1 = model.create_entity("design", at=T(2000))
        d2 = model.create_entity("design", at=T(2001))
        model.attach_alias(d1.entity_id, Alias(kind="class_name", value="Sydney 38",
                                               valid_from=T(2000)))
        model.attach_alias(d2.entity_id, Alias(kind="class_name", value="Sydney 38 OD",
                                               valid_from=T(2001)))
        boat = model.create_entity("boat", at=T(2002))
        # Two sources assert the boat's design under the two spellings/ids.
        a1 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="design",
                         value=str(d1.entity_id), source_slug="irc-certs",
                         recorded_at=T(2003), valid_from=T(2003), confidence=0.9)
        a2 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="design",
                         value=str(d2.entity_id), source_slug="sailsys",
                         recorded_at=T(2004), valid_from=T(2004), confidence=0.6)
        model.assert_about(boat.entity_id, a1)
        model.assert_about(boat.entity_id, a2)
        pre = model.resolve_truth(boat.entity_id, as_of=T(2005))
        assert pre.value("design") == str(d1.entity_id)
        assert pre.conflicts["design"]  # disagreement observable pre-merge
        # Review decision: one class.  Merge d2 into d1.
        model.merge(d1.entity_id, d2.entity_id, at=T(2006), reason="same class")
        assert model._entities[d2.entity_id].merged_into == d1.entity_id
        # Correct the losing source's assertion onto the survivor design,
        # superseding the old claim.  As in the DP-03-02 store, the
        # superseded row is *stamped* with the supersession pointer —
        # history is a pointer update, never a value overwrite.
        a3 = AssertionV1(entity_type="boat", entity_key=boat.entity_key, field="design",
                         value=str(d1.entity_id), source_slug="sailsys",
                         recorded_at=T(2006, 2), valid_from=T(2006, 2),
                         supersedes=a2.assertion_id)
        model.assert_about(boat.entity_id, a3)
        a2_stamped = AssertionV1.from_dict(
            {**a2.to_dict(), "superseded_by": a3.assertion_id,
             "superseded_at": T(2006, 2).isoformat()}
        )
        # Replace the ref target: the store would UPDATE the pointer columns.
        model._assertions[a2_stamped.assertion_id] = a2_stamped
        post = model.resolve_truth(boat.entity_id, as_of=T(2006, 3))
        assert post.value("design") == str(d1.entity_id)
        # Post-merge resolution is unambiguous: the only coexisting claim is
        # the (higher-confidence) certificate one, agreeing with the winner.
        assert [c.assertion_id for c in post.conflicts.get("design", ())] == [a1.assertion_id]
        # History intact: resolving before the merge still shows the conflict.
        assert model.resolve_truth(boat.entity_id, as_of=T(2005)).conflicts["design"]


class TestWalkthrough4CorrectedRaceResult:
    """6.4 — DNF corrected to 3rd on redress; both states must reproduce."""

    def test_dnf_then_redress_bitemporal(self):
        model = DomainModel()
        boat = model.create_entity("boat", at=T(2019, 1))
        event = model.create_entity("event", at=T(2019, 12, 1))
        race = model.create_entity("race", at=T(2019, 12, 26))
        entry = model.create_entity("entry", at=T(2019, 12, 1))
        result = model.create_entity("result", at=T(2019, 12, 28))
        assert event.entity_type is EntityType.EVENT
        assert race.entity_type is EntityType.RACE
        assert entry.entity_type is EntityType.ENTRY
        assert result.entity_type is EntityType.RESULT
        t1, t2 = T(2019, 12, 29), T(2020, 1, 15)
        a1 = AssertionV1(entity_type="result", entity_key=result.entity_key,
                         field="status", value="DNF", source_slug="sailsys",
                         recorded_at=t1, valid_from=t1)
        model.assert_about(result.entity_id, a1)
        assert model.resolve_truth(result.entity_id, as_of=t1 + DAY).value("status") == "DNF"
        a2 = AssertionV1(entity_type="result", entity_key=result.entity_key,
                         field="place", value=3, source_slug="sailsys",
                         recorded_at=t2, valid_from=t1, supersedes=a1.assertion_id)
        # The DNF *status* assertion is superseded by an explicit correction.
        a2b = AssertionV1(entity_type="result", entity_key=result.entity_key,
                          field="status", value="finished", source_slug="sailsys",
                          recorded_at=t2, valid_from=t1, supersedes=a1.assertion_id)
        model.assert_about(result.entity_id, a2)
        model.assert_about(result.entity_id, a2b)
        now = model.resolve_truth(result.entity_id, as_of=t2 + DAY)
        assert now.value("status") == "finished"
        assert now.value("place") == 3
        # The historical publication of the DNF is still reproducible.
        then = model.resolve_truth(result.entity_id, as_of=t1 + DAY)
        assert then.value("status") == "DNF"
        assert then.value("place") is None


class TestWalkthrough5VenueRenameAndOrganiserChange:
    """6.5 — venue rebranded, organising authority changes hands."""

    def test_venue_id_stable_across_renames_and_organisers(self):
        model = DomainModel()
        venue = model.create_entity("venue", at=T(2004, 8))
        model.attach_alias(venue.entity_id, Alias(kind="venue_name", value="Hamilton Island",
                                                  valid_from=T(2004), valid_to=T(2016)))
        model.attach_alias(venue.entity_id, Alias(kind="venue_name", value="Hamilton Island Marina",
                                                  valid_from=T(2016)))
        org_old = model.create_entity("organisation", at=T(2004))
        org_new = model.create_entity("organisation", at=T(2018))
        ev15 = model.create_entity("event", at=T(2015, 8))
        ev19 = model.create_entity("event", at=T(2019, 8))
        # Events reference the venue by opaque id; organiser changes are
        # assertions on the event, not a mutated venue name string.
        for ev, org, when in ((ev15, org_old, T(2015, 8)), (ev19, org_new, T(2019, 8))):
            a = AssertionV1(entity_type="event", entity_key=ev.entity_key,
                            field="organiser", value=str(org.entity_id),
                            source_slug="yachtscoring", recorded_at=when, valid_from=when)
            model.assert_about(ev.entity_id, a)
            a2 = AssertionV1(entity_type="event", entity_key=ev.entity_key,
                             field="venue", value=str(venue.entity_id),
                             source_slug="yachtscoring", recorded_at=when, valid_from=when)
            model.assert_about(ev.entity_id, a2)
        # One venue across the rename.
        assert model.resolve_alias("venue_name", "Hamilton Island", at=T(2010)).entity_id == venue.entity_id
        assert model.resolve_alias("venue_name", "Hamilton Island Marina", at=T(2020)).entity_id == venue.entity_id
        # Both editions resolve to the same venue id, honest organiser history.
        t15 = model.resolve_truth(ev15.entity_id, as_of=T(2016))
        t19 = model.resolve_truth(ev19.entity_id, as_of=T(2020))
        assert t15.value("venue") == t19.value("venue") == str(venue.entity_id)
        assert t15.value("organiser") == str(org_old.entity_id)
        assert t19.value("organiser") == str(org_new.entity_id)
