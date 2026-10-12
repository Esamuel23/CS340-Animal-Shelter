# =============================================================================
# File:     config.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Reads connection settings from the environment and fails fast
#           with a clear message when something is missing. No credential
#           or host name lives in source code.
# =============================================================================
"""Environment-based configuration for the shelter package."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlsplit

from .errors import ConfigurationError

DEFAULT_DB = "aac"
DEFAULT_COLLECTION = "animals"
DEFAULT_TIMEOUT_MS = 5000
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 60000

# Database and collection names: letters, digits, underscore. Keeps odd input
# (dots, dollar signs, slashes) out of namespace names.
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


@dataclass(frozen=True)
class Settings:
    """Connection settings. Build with load_settings(), not by hand."""

    uri: str
    db_name: str = DEFAULT_DB
    collection_name: str = DEFAULT_COLLECTION
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    def __repr__(self) -> str:  # never print the password
        return (
            f"Settings(uri={redact_uri(self.uri)!r}, db_name={self.db_name!r}, "
            f"collection_name={self.collection_name!r}, timeout_ms={self.timeout_ms})"
        )

    __str__ = __repr__


def redact_uri(uri: str) -> str:
    """Return the URI with any user:password replaced by '***' (safe to log)."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return "<unparseable uri>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"***@{host}" if "@" in parts.netloc else host
    return f"{parts.scheme}://{netloc}{parts.path}"


def _read_name(env: Mapping[str, str], key: str, default: str) -> str:
    value = (env.get(key) or default).strip()
    if not _NAME_RE.match(value):
        raise ConfigurationError(f"{key} must be 1-64 letters, digits, or underscores.")
    return value


def load_settings(
    environ: Optional[Mapping[str, str]] = None,
    uri_var: str = "MONGO_URI",
) -> Settings:
    """Build Settings from environment variables.

    MONGO_URI          required, mongodb:// or mongodb+srv://
    MONGO_DB           optional, default "aac"
    MONGO_COLLECTION   optional, default "animals"
    MONGO_TIMEOUT_MS   optional, default 5000 (server selection and connect timeout)

    uri_var lets the setup scripts read MONGO_SETUP_URI first (an account with
    index and validator rights) while the app keeps the narrow MONGO_URI user.
    """
    env = os.environ if environ is None else environ

    uri = (env.get(uri_var) or "").strip()
    if not uri:
        raise ConfigurationError(
            f"{uri_var} is not set. Copy .env.example to .env and fill it in, "
            "or export the variable before starting."
        )
    if not (uri.startswith("mongodb://") or uri.startswith("mongodb+srv://")):
        raise ConfigurationError(f"{uri_var} must start with mongodb:// or mongodb+srv://.")

    raw_timeout = (env.get("MONGO_TIMEOUT_MS") or str(DEFAULT_TIMEOUT_MS)).strip()
    try:
        timeout_ms = int(raw_timeout)
    except ValueError:
        raise ConfigurationError("MONGO_TIMEOUT_MS must be a whole number of milliseconds.") from None
    if not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
        raise ConfigurationError(
            f"MONGO_TIMEOUT_MS must be between {MIN_TIMEOUT_MS} and {MAX_TIMEOUT_MS}."
        )

    return Settings(
        uri=uri,
        db_name=_read_name(env, "MONGO_DB", DEFAULT_DB),
        collection_name=_read_name(env, "MONGO_COLLECTION", DEFAULT_COLLECTION),
        timeout_ms=timeout_ms,
    )


def load_setup_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Settings for scripts/: prefer MONGO_SETUP_URI, fall back to MONGO_URI."""
    env = os.environ if environ is None else environ
    if (env.get("MONGO_SETUP_URI") or "").strip():
        return load_settings(env, uri_var="MONGO_SETUP_URI")
    return load_settings(env)
