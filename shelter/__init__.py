# =============================================================================
# File:     __init__.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Package entry point. Re-exports the public names so callers can
#           write `from shelter import AnimalShelter, ValidationError`.
# =============================================================================
"""Database layer for the Grazioso Salvare animal shelter dashboard."""

from .animal_shelter import AnimalShelter
from .config import Settings, load_settings, load_setup_settings, redact_uri
from .errors import (
    ConfigurationError,
    DatabaseUnavailable,
    PermissionDenied,
    ShelterError,
    ValidationError,
)
from .profiles import ALL_ANIMALS, PROFILE_DEFINITIONS, VALID_PROFILE_KEYS

__all__ = [
    "AnimalShelter",
    "Settings",
    "load_settings",
    "load_setup_settings",
    "redact_uri",
    "ShelterError",
    "ConfigurationError",
    "ValidationError",
    "DatabaseUnavailable",
    "PermissionDenied",
    "ALL_ANIMALS",
    "PROFILE_DEFINITIONS",
    "VALID_PROFILE_KEYS",
]
