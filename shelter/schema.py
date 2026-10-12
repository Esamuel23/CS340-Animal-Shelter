# =============================================================================
# File:     schema.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  The $jsonSchema validator for the animals collection, plus the
#           field list and field types the CRUD layer uses for its whitelist.
#           One source of truth for "what an animal document looks like."
# =============================================================================
"""Schema for aac.animals."""

from __future__ import annotations

SEX_VALUES = ("Intact Male", "Intact Female", "Neutered Male", "Spayed Female", "Unknown")

MAX_STRING = 200

# Field -> Python type the CRUD layer coerces to. Every field a caller may read,
# filter, insert, or update must be listed here. Anything else is rejected.
STRING_FIELDS = (
    "age_upon_outcome",
    "animal_id",
    "animal_type",
    "breed",
    "color",
    "date_of_birth",
    "datetime",
    "monthyear",
    "name",
    "outcome_subtype",
    "outcome_type",
    "sex_upon_outcome",
)
FLOAT_FIELDS = ("location_lat", "location_long", "age_upon_outcome_in_weeks")
INT_FIELDS = ("rec_num",)

FIELD_TYPES = {
    **{f: str for f in STRING_FIELDS},
    **{f: float for f in FLOAT_FIELDS},
    **{f: int for f in INT_FIELDS},
}
ALLOWED_FIELDS = frozenset(FIELD_TYPES)

# "location" is derived from lat/long by the CRUD layer and setup script; callers
# never send it directly.
DERIVED_FIELDS = frozenset({"location"})

REQUIRED_FIELDS = ("animal_id", "animal_type", "breed", "sex_upon_outcome", "age_upon_outcome_in_weeks")

_NUMBER = ["double", "int", "long", "decimal"]


def _string(max_len: int = MAX_STRING) -> dict:
    return {"bsonType": "string", "maxLength": max_len}


ANIMAL_JSON_SCHEMA = {
    "bsonType": "object",
    "title": "AAC animal outcome record",
    "required": list(REQUIRED_FIELDS),
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "rec_num": {"bsonType": ["int", "long"], "minimum": 0},
        "age_upon_outcome": _string(),
        "animal_id": {"bsonType": "string", "minLength": 1, "maxLength": 20},
        "animal_type": {"bsonType": "string", "minLength": 1, "maxLength": 50},
        "breed": {"bsonType": "string", "minLength": 1, "maxLength": MAX_STRING},
        "color": _string(),
        "date_of_birth": _string(),
        "datetime": _string(),
        "monthyear": _string(),
        "name": _string(),
        "outcome_subtype": _string(),
        "outcome_type": _string(),
        "sex_upon_outcome": {"enum": list(SEX_VALUES)},
        "age_upon_outcome_in_weeks": {"bsonType": _NUMBER, "minimum": 0},
        "location_lat": {"bsonType": _NUMBER, "minimum": -90, "maximum": 90},
        "location_long": {"bsonType": _NUMBER, "minimum": -180, "maximum": 180},
        "location": {
            "bsonType": "object",
            "required": ["type", "coordinates"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["Point"]},
                "coordinates": {
                    "bsonType": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"bsonType": _NUMBER},
                },
            },
        },
    },
}

VALIDATOR = {"$jsonSchema": ANIMAL_JSON_SCHEMA}
VALIDATION_LEVEL = "strict"   # check every insert and update
VALIDATION_ACTION = "error"   # reject, don't just warn

# Index definitions shared by scripts/setup_db.py and scripts/explain_report.py.
#
# profile_esr follows the ESR guideline: Equality fields first (sex, breed),
# then the Sort/Range field (age). _id is last so the tie-break in the page
# sort {age: 1, _id: 1} is also read from the index and no in-memory SORT is
# needed for stable paging.
PROFILE_INDEX_NAME = "profile_esr"
PROFILE_INDEX_KEYS = [
    ("sex_upon_outcome", 1),
    ("breed", 1),
    ("age_upon_outcome_in_weeks", 1),
    ("_id", 1),
]
GEO_INDEX_NAME = "location_2dsphere"
GEO_INDEX_KEYS = [("location", "2dsphere")]

ANIMAL_INDEXES = (
    (PROFILE_INDEX_NAME, PROFILE_INDEX_KEYS),
    (GEO_INDEX_NAME, GEO_INDEX_KEYS),
)
