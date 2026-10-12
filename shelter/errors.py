# =============================================================================
# File:     errors.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Typed errors for the shelter package. Callers catch these instead
#           of raw pymongo exceptions, and the messages never echo driver
#           internals, hostnames, or credentials.
# =============================================================================
"""Error hierarchy for the shelter package."""


class ShelterError(Exception):
    """Base class. Catch this to handle every shelter failure in one place."""


class ConfigurationError(ShelterError):
    """Settings are missing or malformed (for example MONGO_URI is not set)."""


class ValidationError(ShelterError):
    """Input was rejected before or by the database (bad key, type, or schema)."""


class DatabaseUnavailable(ShelterError):
    """The server could not be reached in time, or the network dropped."""


class PermissionDenied(ShelterError):
    """The database user is not allowed to do this. Least privilege working as designed."""
