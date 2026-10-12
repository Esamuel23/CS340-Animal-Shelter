# =============================================================================
# File:     test_config.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Configuration tests: fail fast on missing or malformed settings,
#           and never print a password.
# =============================================================================
"""Spec test 1 (missing MONGO_URI) plus related config checks."""

import pytest

from shelter import AnimalShelter, ConfigurationError, load_settings
from shelter.config import load_setup_settings, redact_uri


def test_missing_mongo_uri_raises_configuration_error():
    with pytest.raises(ConfigurationError, match="MONGO_URI is not set"):
        load_settings({})


def test_from_env_without_uri_fails_before_touching_the_driver():
    with pytest.raises(ConfigurationError):
        AnimalShelter.from_env({"MONGO_DB": "aac"})


def test_blank_uri_counts_as_missing():
    with pytest.raises(ConfigurationError):
        load_settings({"MONGO_URI": "   "})


def test_uri_must_be_a_mongodb_scheme():
    with pytest.raises(ConfigurationError, match="mongodb://"):
        load_settings({"MONGO_URI": "http://example.com"})


@pytest.mark.parametrize("value", ["abc", "50", "999999"])
def test_timeout_must_be_a_sane_number(value):
    with pytest.raises(ConfigurationError, match="MONGO_TIMEOUT_MS"):
        load_settings({"MONGO_URI": "mongodb://localhost", "MONGO_TIMEOUT_MS": value})


@pytest.mark.parametrize("name", ["aac.animals", "$cmd", "a/b", ""])
def test_collection_name_is_restricted(name):
    env = {"MONGO_URI": "mongodb://localhost", "MONGO_COLLECTION": name}
    if name == "":
        assert load_settings(env).collection_name == "animals"  # empty falls back to default
    else:
        with pytest.raises(ConfigurationError):
            load_settings(env)


def test_defaults():
    s = load_settings({"MONGO_URI": "mongodb://localhost"})
    assert (s.db_name, s.collection_name, s.timeout_ms) == ("aac", "animals", 5000)


def test_password_never_appears_in_repr_or_str():
    s = load_settings({"MONGO_URI": "mongodb+srv://shelter_app:S3cretPw@cluster0.example.mongodb.net/"})
    assert "S3cretPw" not in repr(s)
    assert "S3cretPw" not in str(s)
    assert redact_uri(s.uri) == "mongodb+srv://***@cluster0.example.mongodb.net/"


def test_setup_settings_prefer_setup_uri():
    env = {"MONGO_URI": "mongodb://app@h", "MONGO_SETUP_URI": "mongodb://setup@h"}
    assert load_setup_settings(env).uri == "mongodb://setup@h"
    assert load_setup_settings({"MONGO_URI": "mongodb://app@h"}).uri == "mongodb://app@h"
