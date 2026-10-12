# =============================================================================
# File:     test_queries.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Query correctness on seeded fixtures: rescue profiles, paging,
#           breed counts, CRUD return contracts, and teardown.
# =============================================================================
"""Spec tests 6, 7, 8, 10 (unit level, mongomock)."""

import time

import pytest

from shelter import AnimalShelter, DatabaseUnavailable, PROFILE_DEFINITIONS, ShelterError, ValidationError
from shelter.animal_shelter import TABLE_COLUMNS, build_profile_pipeline
from shelter.config import Settings


def expected_ids(animals, key):
    p = PROFILE_DEFINITIONS[key]
    return {
        a["animal_id"]
        for a in animals
        if a["breed"] in p.breeds and a["sex_upon_outcome"] == p.sex
        and p.min_weeks <= a["age_upon_outcome_in_weeks"] <= p.max_weeks
    }


# -- Spec test 6: each profile returns only matching animals -------------------

@pytest.mark.parametrize("key", list(PROFILE_DEFINITIONS))
def test_profile_returns_only_matching_animals(mock_shelter, fixture_animals, key):
    p = PROFILE_DEFINITIONS[key]
    rows = mock_shelter.query_by_profile(key, page=0, page_size=100)
    assert rows, "fixture should have matches for every profile"
    for r in rows:
        assert r["breed"] in p.breeds
        assert r["sex_upon_outcome"] == p.sex
        assert p.min_weeks <= r["age_upon_outcome_in_weeks"] <= p.max_weeks
    assert {r["animal_id"] for r in rows} == expected_ids(fixture_animals, key)


def test_profile_boundaries_are_inclusive(mock_shelter):
    ages = [r["age_upon_outcome_in_weeks"] for r in mock_shelter.query_by_profile("water", 0, 100)]
    assert 26.0 in ages and 156.0 in ages
    assert 25.9 not in ages and 156.1 not in ages


def test_expected_fixture_counts(mock_shelter):
    # Hand-checked against conftest.py: 14 water, 4 mountain, 5 disaster.
    assert mock_shelter.count_by_profile("water") == 14
    assert mock_shelter.count_by_profile("mountain") == 4
    assert mock_shelter.count_by_profile("disaster") == 5


def test_rows_are_sorted_youngest_first_and_projected(mock_shelter):
    rows = mock_shelter.query_by_profile("water", 0, 100)
    ages = [r["age_upon_outcome_in_weeks"] for r in rows]
    assert ages == sorted(ages)
    for r in rows:
        assert "_id" not in r and "location" not in r
        assert set(r) <= set(TABLE_COLUMNS)


def test_all_profile_returns_everything_paged(mock_shelter, fixture_animals):
    assert mock_shelter.count_by_profile("all") == len(fixture_animals)
    assert mock_shelter.count_by_profile(None) == len(fixture_animals)
    assert len(mock_shelter.query_by_profile("all", 0, 10)) == 10


# -- Spec test 7: paging -------------------------------------------------------

def test_pages_do_not_overlap_and_cover_everything(mock_shelter):
    pages = [mock_shelter.query_by_profile("water", page=i, page_size=5) for i in range(4)]
    ids = [[r["animal_id"] for r in page] for page in pages]
    assert [len(p) for p in ids] == [5, 5, 4, 0]
    flat = [i for page in ids for i in page]
    assert len(flat) == len(set(flat)) == 14


@pytest.mark.parametrize("page,size", [(-1, 10), (0, 0), (0, 101), (0, -5), ("1", 10), (0, True), (10_001, 10)])
def test_paging_bounds_enforced(mock_shelter, page, size):
    with pytest.raises(ValidationError):
        mock_shelter.query_by_profile("water", page=page, page_size=size)


def test_pipeline_shape_matches_the_design():
    stages = [next(iter(s)) for s in build_profile_pipeline("water", 2, 10)]
    assert stages == ["$match", "$sort", "$skip", "$limit", "$project"]
    assert build_profile_pipeline("water", 2, 10)[2] == {"$skip": 20}


# -- Spec test 8: breed counts ---------------------------------------------------

@pytest.mark.parametrize("key", ["all", *PROFILE_DEFINITIONS])
def test_breed_counts_sum_to_profile_total(mock_shelter, key):
    counts = mock_shelter.breed_counts(key)
    assert sum(c["count"] for c in counts) == mock_shelter.count_by_profile(key)
    assert [c["count"] for c in counts] == sorted((c["count"] for c in counts), reverse=True)
    assert all(set(c) == {"breed", "count"} for c in counts)


def test_breed_counts_top_n(mock_shelter):
    assert len(mock_shelter.breed_counts("all", top=2)) == 2


# -- CRUD contract (same return values as CS 340) ------------------------------

def test_crud_round_trip(mock_shelter):
    new = {"animal_id": "T9001", "animal_type": "Dog", "breed": "Newfoundland",
           "sex_upon_outcome": "Intact Female", "age_upon_outcome_in_weeks": "40",
           "location_lat": 30.5, "location_long": -97.5}
    assert mock_shelter.create(new) is True
    stored = mock_shelter.collection.find_one({"animal_id": "T9001"})
    assert stored["age_upon_outcome_in_weeks"] == 40.0                    # coerced from "40"
    assert stored["location"] == {"type": "Point", "coordinates": [-97.5, 30.5]}  # derived, lon first
    assert mock_shelter.read({"animal_id": "T9001"})[0]["breed"] == "Newfoundland"
    assert mock_shelter.update({"animal_id": "T9001"}, {"name": "Nessie"}) == 1
    assert mock_shelter.update({"animal_id": "T9001"}, {"location_lat": 30.6, "location_long": -97.4}) == 1
    assert mock_shelter.collection.find_one({"animal_id": "T9001"})["location"]["coordinates"] == [-97.4, 30.6]
    assert mock_shelter.delete({"animal_id": "T9001"}) == 1
    assert mock_shelter.read({"animal_id": "T9001"}) == []


# -- Spec test 10: context manager closes the client ---------------------------

class FakeClient:
    def __init__(self):
        self.closed = 0

    def __getitem__(self, name):
        return {"animals": object()}

    def close(self):
        self.closed += 1


def test_context_manager_closes_client():
    fake = FakeClient()
    with AnimalShelter(Settings(uri="mongodb://fake"), client=fake) as shelter:
        assert not shelter.closed
    assert fake.closed == 1 and shelter.closed
    shelter.close()                      # second close is a no-op
    assert fake.closed == 1


def test_context_manager_closes_on_error():
    fake = FakeClient()
    with pytest.raises(RuntimeError):
        with AnimalShelter(Settings(uri="mongodb://fake"), client=fake):
            raise RuntimeError("boom")
    assert fake.closed == 1


def test_closed_shelter_refuses_work(mock_shelter):
    mock_shelter.close()
    with pytest.raises(ShelterError, match="closed"):
        mock_shelter.read({"animal_id": "T0001"})


# -- Timeouts and safe errors (no server needed: port 1 refuses connections) ---

def test_unreachable_host_fails_fast_with_typed_error():
    settings = Settings(uri="mongodb://127.0.0.1:1/?directConnection=true", timeout_ms=500)
    start = time.monotonic()
    with AnimalShelter(settings) as shelter:
        with pytest.raises(DatabaseUnavailable) as info:
            shelter.query_by_profile("water")
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"took {elapsed:.1f}s"
    message = str(info.value)
    assert "127.0.0.1" not in message and "ServerSelectionTimeoutError" not in message
    assert info.value.__cause__ is None and info.value.__suppress_context__
