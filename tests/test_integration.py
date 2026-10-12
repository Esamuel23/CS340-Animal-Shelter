# =============================================================================
# File:     test_integration.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Tests that need a real MongoDB server (test database only):
#           the $jsonSchema validator, least-privilege refusals, index use,
#           geo queries, and the same profile/paging checks on a real engine.
# =============================================================================
"""Integration tests. Run with MONGO_TEST_URI (and MONGO_TEST_APP_URI) set.

    pytest -m integration
"""

import pytest

from shelter import AnimalShelter, PermissionDenied, ValidationError
from shelter.animal_shelter import _db_errors, build_profile_pipeline
from shelter.profiles import PROFILE_DEFINITIONS

pytestmark = pytest.mark.integration

GOOD = {
    "animal_id": "T8000",
    "animal_type": "Dog",
    "breed": "Newfoundland",
    "sex_upon_outcome": "Intact Female",
    "age_upon_outcome_in_weeks": 40,
    "location_lat": 30.5,
    "location_long": -97.5,
}


# -- Spec test 9: the validator ------------------------------------------------

def test_validator_rejects_negative_age(live_shelter):
    """The Python layer lets a negative number through; the database refuses it."""
    with pytest.raises(ValidationError, match="schema validation"):
        live_shelter.create({**GOOD, "age_upon_outcome_in_weeks": -7.04})
    assert live_shelter.read({"animal_id": "T8000"}) == []


@pytest.mark.parametrize(
    "change",
    [
        {"sex_upon_outcome": "Female"},          # not in the enum
        {"location_lat": 91.0},                  # out of range
        {"breed": ""},                           # minLength 1
    ],
)
def test_validator_rejects_bad_values(live_shelter, change):
    with pytest.raises(ValidationError):
        live_shelter.create({**GOOD, **change})


def test_validator_rejects_missing_required_and_extra_fields(live_shelter):
    coll = live_shelter.collection
    no_breed = {k: v for k, v in GOOD.items() if k != "breed"}
    with pytest.raises(ValidationError):
        live_shelter.create(no_breed)
    # A write that skips the Python layer is still checked by the server.
    with pytest.raises(ValidationError):
        with _db_errors("insert"):
            coll.insert_one({**GOOD, "is_admin": True})


def test_validator_applies_to_updates(live_shelter):
    with pytest.raises(ValidationError):
        live_shelter.update({"animal_id": "T0001"}, {"age_upon_outcome_in_weeks": -1})
    assert live_shelter.read({"animal_id": "T0001"})[0]["age_upon_outcome_in_weeks"] == 26.0


def test_valid_document_is_accepted(live_shelter):
    assert live_shelter.create(GOOD) is True
    assert live_shelter.read({"animal_id": "T8000"})[0]["breed"] == "Newfoundland"


# -- Least privilege -----------------------------------------------------------

def test_app_user_can_read_but_not_administer(live_shelter, live_app_settings):
    with AnimalShelter(live_app_settings) as app:
        assert app.count_by_profile("water") == 14          # find works
        for forbidden in (
            lambda: app.collection.drop(),
            lambda: app.collection.create_index("name"),
            lambda: app.client[live_app_settings.db_name].command("collMod", "animals", validator={}),
            lambda: app.client["admin"]["system.users"].find_one(),
        ):
            with pytest.raises(PermissionDenied):
                with _db_errors("do an admin task"):
                    forbidden()
    assert live_shelter.collection.estimated_document_count() > 0


def test_app_user_can_write_animals(live_shelter, live_app_settings):
    with AnimalShelter(live_app_settings) as app:
        assert app.create(GOOD) is True
        assert app.update({"animal_id": "T8000"}, {"name": "Nessie"}) == 1
        assert app.delete({"animal_id": "T8000"}) == 1


# -- Same correctness checks on the real engine ---------------------------------

@pytest.mark.parametrize("key", list(PROFILE_DEFINITIONS))
def test_profile_matches_on_real_server(live_shelter, key):
    p = PROFILE_DEFINITIONS[key]
    rows = live_shelter.query_by_profile(key, 0, 100)
    assert rows
    for r in rows:
        assert r["breed"] in p.breeds and r["sex_upon_outcome"] == p.sex
        assert p.min_weeks <= r["age_upon_outcome_in_weeks"] <= p.max_weeks
    assert sum(c["count"] for c in live_shelter.breed_counts(key)) == len(rows)


def test_paging_on_real_server(live_shelter):
    pages = [[r["animal_id"] for r in live_shelter.query_by_profile("water", i, 5)] for i in range(3)]
    flat = [x for p in pages for x in p]
    assert [len(p) for p in pages] == [5, 5, 4] and len(set(flat)) == 14


# -- Indexes are actually used ---------------------------------------------------

def test_profile_query_uses_compound_index(live_shelter):
    db = live_shelter.client[live_shelter.settings.db_name]
    explain = db.command(
        {
            "explain": {"aggregate": "animals", "pipeline": build_profile_pipeline("water"), "cursor": {}},
            "verbosity": "executionStats",
        }
    )
    text = str(explain["queryPlanner"]["winningPlan"])
    assert "IXSCAN" in text and "profile_esr" in text and "COLLSCAN" not in text
    stats = explain["executionStats"]
    assert stats["totalDocsExamined"] == stats["nReturned"] == 14


def test_animals_near_uses_2dsphere(live_shelter):
    rows = live_shelter.animals_near(30.40, -97.60, max_meters=1000, limit=50)
    assert rows and all(abs(r["location_lat"] - 30.40) < 0.01 for r in rows)
    far = live_shelter.animals_near(30.70, -97.30, max_meters=1000, limit=50)
    assert [r["animal_id"] for r in far] == ["T0032"]
    with pytest.raises(ValidationError):
        live_shelter.animals_near(95, -97.3)


# -- Employer portal on a real server (strand E) ---------------------------------

def test_portal_sessions_and_visits_with_app_user(live_shelter, live_app_settings):
    from scripts.setup_db import ensure_session_index, ensure_visits_index
    from shelter.portal import EmployerPortal

    admin_db = live_shelter.client[live_shelter.settings.db_name]
    admin_db.drop_collection("visits")
    admin_db.drop_collection("portal_sessions")
    ensure_visits_index(admin_db)
    ensure_session_index(admin_db)
    try:
        with AnimalShelter(live_app_settings) as app:
            portal = EmployerPortal(app.client[live_app_settings.db_name])
            token = portal.start_session("Ada Lovelace", "Example Co")   # app user: insert sessions + visits
            assert portal.lookup(token)["company"] == "Example Co"
            portal.record_visit("Ada Lovelace", "Example Co", "view")
            report = portal.engagement()
            assert report[0]["company"] == "Example Co" and report[0]["events"] == 2
            portal.end_session(token)
            assert portal.lookup(token) is None
        plan = admin_db.command({
            "explain": {"find": "visits", "filter": {"company": "Example Co"}, "sort": {"timestamp": -1}},
            "verbosity": "queryPlanner",
        })["queryPlanner"]["winningPlan"]
        assert "company_timestamp" in str(plan) and "SORT" not in str(plan).replace("SORT_", "")
        ttl = admin_db["portal_sessions"].index_information()["session_ttl"]
        assert ttl["expireAfterSeconds"] == 0
    finally:
        admin_db.drop_collection("visits")
        admin_db.drop_collection("portal_sessions")
