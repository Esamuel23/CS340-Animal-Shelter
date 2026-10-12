# =============================================================================
# File:     conftest.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Shared pytest fixtures. Unit tests run on mongomock (no server).
#           Integration tests run on a real mongod, in a separate TEST
#           database only, and are skipped when MONGO_TEST_URI is not set.
# =============================================================================
"""Fixtures for the shelter test suite.

The FIXTURE_ANIMALS below are synthetic test records (animal_id prefix "T").
They are not shelter data; they exist to hit each profile's edges: exact
boundary ages, one week outside, wrong sex, and breeds outside the list.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import mongomock
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shelter.animal_shelter import AnimalShelter, geo_point  # noqa: E402
from shelter.config import Settings  # noqa: E402

PRODUCTION_DB_NAMES = {"aac", "AAC"}


def _animal(n, breed, sex, weeks, lat=30.40, lon=-97.60, animal_type="Dog"):
    return {
        "rec_num": n,
        "animal_id": f"T{n:04d}",
        "animal_type": animal_type,
        "breed": breed,
        "color": "Test",
        "name": f"Test{n}",
        "sex_upon_outcome": sex,
        "age_upon_outcome": "test",
        "age_upon_outcome_in_weeks": float(weeks),
        "outcome_type": "Adoption",
        "location_lat": lat,
        "location_long": lon,
        "location": geo_point(lat, lon),
    }


def build_fixture_animals():
    rows = []
    n = 0

    def add(*args, **kw):
        nonlocal n
        n += 1
        rows.append(_animal(n, *args, **kw))

    # Water: 12 matches (enough for three pages of 5), including both boundaries.
    for w in (26, 30, 40, 52, 60, 70, 80, 90, 100, 120, 140, 156):
        add("Labrador Retriever Mix", "Intact Female", w)
    add("Chesapeake Bay Retriever", "Intact Female", 50)
    add("Newfoundland", "Intact Female", 75)
    # Water near misses.
    add("Labrador Retriever Mix", "Intact Female", 25.9)    # too young
    add("Labrador Retriever Mix", "Intact Female", 156.1)   # too old
    add("Labrador Retriever Mix", "Spayed Female", 60)      # wrong sex
    add("Labrador Retriever", "Intact Female", 60)          # breed not on the list
    # Mountain: matches and misses.
    add("German Shepherd", "Intact Male", 26)
    add("Alaskan Malamute", "Intact Male", 100)
    add("Old English Sheepdog", "Intact Male", 156)
    add("Siberian Husky", "Intact Male", 80)
    add("Siberian Husky", "Intact Female", 80)               # wrong sex
    add("Siberian Husky", "Intact Male", 157)                # too old for mountain, ok for nothing else
    # Disaster: overlaps with mountain on German Shepherd / Rottweiler.
    add("Rottweiler", "Intact Male", 20)                     # disaster only (too young for mountain)
    add("Rottweiler", "Intact Male", 300)                    # disaster only (too old for mountain)
    add("Doberman Pinscher", "Intact Male", 200)
    add("Bloodhound", "Intact Male", 150)
    add("Golden Retriever", "Intact Male", 19.9)             # too young
    add("Golden Retriever", "Neutered Male", 100)            # wrong sex
    # Other animals.
    add("Domestic Shorthair Mix", "Spayed Female", 52, animal_type="Cat")
    add("Domestic Shorthair Mix", "Neutered Male", 10, animal_type="Cat", lat=30.70, lon=-97.30)
    return rows


FIXTURE_ANIMALS = build_fixture_animals()


@pytest.fixture
def fixture_animals():
    return [dict(a) for a in FIXTURE_ANIMALS]


@pytest.fixture
def mock_shelter(fixture_animals):
    """AnimalShelter over an in-memory mongomock collection seeded with fixtures."""
    client = mongomock.MongoClient()
    client["aac"]["animals"].insert_many(fixture_animals)
    shelter = AnimalShelter(Settings(uri="mongodb://mock", db_name="aac", collection_name="animals"), client=client)
    yield shelter
    shelter.close()


# ---------------------------------------------------------------------------
# Integration (real mongod, test database only)
# ---------------------------------------------------------------------------

def _test_settings(uri_var: str) -> Settings:
    uri = os.environ.get(uri_var, "").strip()
    if not uri:
        pytest.skip(f"{uri_var} not set; integration test skipped")
    db_name = os.environ.get("MONGO_TEST_DB", "aac_test")
    if db_name in PRODUCTION_DB_NAMES:
        pytest.fail("MONGO_TEST_DB must not be the production database")
    return Settings(uri=uri, db_name=db_name, collection_name="animals", timeout_ms=3000)


@pytest.fixture
def live_shelter(fixture_animals):
    """AnimalShelter on a real server: validator + indexes applied, fixtures seeded.

    Uses MONGO_TEST_URI (setup-level account on the test DB). Drops the test
    collection afterwards.
    """
    from scripts.setup_db import ensure_indexes, ensure_validator

    settings = _test_settings("MONGO_TEST_URI")
    shelter = AnimalShelter(settings)
    db = shelter.client[settings.db_name]
    db.drop_collection(settings.collection_name)
    ensure_validator(db, settings.collection_name)
    ensure_indexes(db[settings.collection_name])
    db[settings.collection_name].insert_many(fixture_animals)
    yield shelter
    if not shelter.closed:
        db.drop_collection(settings.collection_name)
        shelter.close()
    else:
        with AnimalShelter(settings) as cleanup:
            cleanup.client[settings.db_name].drop_collection(settings.collection_name)


@pytest.fixture
def live_app_settings():
    """Settings for the least-privilege app user on the test DB."""
    return _test_settings("MONGO_TEST_APP_URI")
