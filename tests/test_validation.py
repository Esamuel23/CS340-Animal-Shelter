# =============================================================================
# File:     test_validation.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Input-hardening tests: profile whitelist, operator injection,
#           empty filters on update/delete, empty creates, type coercion.
# =============================================================================
"""Spec tests 2, 3, 4, 5 plus the field whitelist and coercion rules."""

import pytest

from shelter import ValidationError
from shelter.animal_shelter import clean_filter, coerce_value, reject_operator_keys


# -- Spec test 2: profile whitelist ------------------------------------------

@pytest.mark.parametrize("bad", ["swamp", "WATER", "", 42, {"$ne": None}, ["water"]])
def test_unknown_profile_is_rejected(mock_shelter, bad):
    with pytest.raises(ValidationError):
        mock_shelter.query_by_profile(bad)
    with pytest.raises(ValidationError):
        mock_shelter.count_by_profile(bad)
    with pytest.raises(ValidationError):
        mock_shelter.breed_counts(bad)


# -- Spec test 3: operator injection -----------------------------------------

INJECTIONS = [
    {"$where": "sleep(5000) || true"},                       # top-level JS operator
    {"breed": {"$ne": None}},                                 # nested: match everything
    {"name": {"$regex": ".*"}},                               # nested regex
    {"$or": [{"breed": "x"}, {"breed": {"$exists": True}}]},  # top-level logical
    {"breed": {"inner": {"$gt": ""}}},                        # $ two levels down
    {"breed": [{"$gt": ""}]},                                 # $ inside a list
]


@pytest.mark.parametrize("query", INJECTIONS)
def test_operator_keys_rejected_by_read(mock_shelter, query):
    # match= pins the $-key layer. Without it the whitelist and the plain-value
    # rule also reject these, so a broken $ check would go unnoticed.
    with pytest.raises(ValidationError, match="Operators are not allowed"):
        mock_shelter.read(query)


@pytest.mark.parametrize("value", INJECTIONS + [{"ok": [{"deep": {"deeper": {"$gt": 1}}}]}])
def test_reject_operator_keys_directly(value):
    with pytest.raises(ValidationError, match="Operators are not allowed"):
        reject_operator_keys(value)


def test_reject_operator_keys_allows_plain_documents():
    reject_operator_keys({"breed": "Newfoundland", "tags": ["a", {"b": 1}], "price": "$5"})


@pytest.mark.parametrize("query", INJECTIONS)
def test_operator_keys_rejected_by_update_and_delete(mock_shelter, query):
    before = mock_shelter.collection.count_documents({})
    with pytest.raises(ValidationError, match="Operators are not allowed"):
        mock_shelter.update(query, {"name": "x"})
    with pytest.raises(ValidationError, match="Operators are not allowed"):
        mock_shelter.delete(query)
    assert mock_shelter.collection.count_documents({}) == before


def test_operator_keys_rejected_in_documents(mock_shelter):
    with pytest.raises(ValidationError):
        mock_shelter.create({"animal_id": "T9", "breed": {"$gt": ""}})
    with pytest.raises(ValidationError):
        mock_shelter.update({"animal_id": "T0001"}, {"$unset": {"breed": ""}})


@pytest.mark.parametrize("query", [{"password": "x"}, {"location.coordinates": 1}, {"_id": "abc"}])
def test_unknown_or_dotted_fields_rejected(mock_shelter, query):
    with pytest.raises(ValidationError):
        mock_shelter.read(query)


def test_injection_does_not_widen_results(mock_shelter):
    """The classic {"$ne": null} trick would have returned every document."""
    with pytest.raises(ValidationError):
        mock_shelter.read({"animal_id": {"$ne": None}})
    assert mock_shelter.read({"animal_id": "T0001"})[0]["animal_id"] == "T0001"


# -- Spec test 4: no mass writes ---------------------------------------------

@pytest.mark.parametrize("empty", [{}, None])
def test_update_and_delete_refuse_empty_filters(mock_shelter, empty):
    before = mock_shelter.collection.count_documents({})
    with pytest.raises(ValidationError, match="empty filter"):
        mock_shelter.update(empty, {"name": "everyone"})
    with pytest.raises(ValidationError, match="empty filter"):
        mock_shelter.delete(empty)
    assert mock_shelter.collection.count_documents({}) == before
    assert mock_shelter.collection.count_documents({"name": "everyone"}) == 0


def test_update_requires_values(mock_shelter):
    for values in ({}, None):
        with pytest.raises(ValidationError):
            mock_shelter.update({"animal_id": "T0001"}, values)


# -- Spec test 5: create input -----------------------------------------------

@pytest.mark.parametrize("data", [{}, None, "not a dict", []])
def test_create_rejects_empty_or_non_dict(mock_shelter, data):
    with pytest.raises(ValidationError):
        mock_shelter.create(data)


@pytest.mark.parametrize("field", ["_id", "location"])
def test_create_rejects_managed_fields(mock_shelter, field):
    with pytest.raises(ValidationError):
        mock_shelter.create({"animal_id": "T9", field: "x"})


# -- Type coercion -------------------------------------------------------------

def test_numeric_strings_are_coerced():
    assert clean_filter({"age_upon_outcome_in_weeks": "26"}) == {"age_upon_outcome_in_weeks": 26.0}
    assert clean_filter({"rec_num": "7"}) == {"rec_num": 7}


def test_numbers_coerced_to_text_for_text_fields():
    assert coerce_value("animal_id", 123) == "123"


@pytest.mark.parametrize(
    "field,value",
    [
        ("age_upon_outcome_in_weeks", "abc"),
        ("age_upon_outcome_in_weeks", float("nan")),
        ("age_upon_outcome_in_weeks", float("inf")),
        ("age_upon_outcome_in_weeks", True),
        ("rec_num", "1.5"),
        ("rec_num", "--5"),
        ("breed", None),
        ("breed", "x" * 201),
        ("breed", "nul\x00byte"),
        ("breed", ["a", "b"]),
    ],
)
def test_bad_values_rejected(field, value):
    with pytest.raises(ValidationError):
        coerce_value(field, value)


def test_read_with_coerced_value_finds_the_record(mock_shelter):
    rows = mock_shelter.read({"age_upon_outcome_in_weeks": "25.9"})
    assert [r["animal_id"] for r in rows] == ["T0015"]


def test_read_limit_is_bounded(mock_shelter):
    assert len(mock_shelter.read(None, limit=3)) == 3
    for bad in (0, 1001, "10", True):
        with pytest.raises(ValidationError):
            mock_shelter.read({}, limit=bad)


def test_update_location_requires_both_coordinates(mock_shelter):
    with pytest.raises(ValidationError, match="together"):
        mock_shelter.update({"animal_id": "T0001"}, {"location_lat": 30.5})
