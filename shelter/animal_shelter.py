# =============================================================================
# File:     animal_shelter.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  CRUD and query layer for the AAC animals collection. Owns every
#           database call the dashboard makes. Validates input before it
#           reaches MongoDB, turns driver exceptions into typed errors, and
#           pushes filtering, paging, and counting into aggregation pipelines.
# =============================================================================
"""AnimalShelter: the only module that talks to MongoDB.

Enhanced from the CS 340 version (see original/animal_shelter.py):
  * connection comes from the environment (MONGO_URI), never from source code
  * timeouts on server selection and connect, plus close() and a context manager
  * read/update/delete filters are checked against a field whitelist; any key
    that starts with "$" is rejected at any depth, so a caller cannot inject
    operators such as $where, $ne, or $regex
  * update and delete refuse an empty filter (the original delete({}) would
    have removed every document); create refuses None and {}
  * rescue-profile queries run as aggregation pipelines with server-side paging
"""

from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Mapping, Optional

from pymongo import MongoClient
from pymongo import errors as pymongo_errors

from .config import Settings, load_settings
from .errors import (
    ConfigurationError,
    DatabaseUnavailable,
    PermissionDenied,
    ShelterError,
    ValidationError,
)
from .profiles import match_criteria
from .schema import ALLOWED_FIELDS, DERIVED_FIELDS, FIELD_TYPES, MAX_STRING

logger = logging.getLogger(__name__)

# Columns the dashboard table shows. Everything else stays in the database.
TABLE_COLUMNS = (
    "animal_id",
    "name",
    "animal_type",
    "breed",
    "color",
    "sex_upon_outcome",
    "age_upon_outcome",
    "age_upon_outcome_in_weeks",
    "outcome_type",
    "location_lat",
    "location_long",
)
TABLE_PROJECTION = {**{c: 1 for c in TABLE_COLUMNS}, "_id": 0}

# Youngest first. _id breaks ties so skip/limit pages never overlap or repeat.
PROFILE_SORT = {"age_upon_outcome_in_weeks": 1, "_id": 1}

MAX_PAGE_SIZE = 100
MAX_PAGE = 10_000
DEFAULT_READ_LIMIT = 100
MAX_READ_LIMIT = 1000

# MongoDB server error codes this module maps to typed errors.
_UNAUTHORIZED = 13
_AUTH_FAILED = 18
_DOC_VALIDATION_FAILED = 121


# ---------------------------------------------------------------------------
# Input checks (pure functions, no database needed)
# ---------------------------------------------------------------------------

def reject_operator_keys(value: Any, where: str = "input") -> None:
    """Raise ValidationError if any dict key at any depth starts with '$'.

    Also rejects non-string keys, dotted keys, and NUL bytes, which MongoDB
    would read as paths or truncate.
    """
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"Field names in {where} must be strings.")
            if key.startswith("$"):
                raise ValidationError(f"Operators are not allowed in {where}.")
            if "." in key or "\x00" in key:
                raise ValidationError(f"Dotted or NUL field names are not allowed in {where}.")
            reject_operator_keys(inner, where)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            reject_operator_keys(inner, where)


def _require_int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be a whole number.")
    if not low <= value <= high:
        raise ValidationError(f"{name} must be between {low} and {high}.")
    return value


def coerce_value(field: str, value: Any) -> Any:
    """Coerce one value to the field's declared type or raise ValidationError.

    Only plain scalars are accepted. Dicts and lists are refused outright, which
    is what stops {"breed": {"$ne": None}} even if the $ check were bypassed.
    """
    expected = FIELD_TYPES[field]
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        raise ValidationError(f"{field} must be a plain {expected.__name__} value.")

    if expected is str:
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be text.")
        if len(value) > MAX_STRING or "\x00" in value:
            raise ValidationError(f"{field} must be at most {MAX_STRING} characters.")
        return value

    if expected is float:
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                raise ValidationError(f"{field} must be a number.") from None
        if not isinstance(value, (int, float)):
            raise ValidationError(f"{field} must be a number.")
        value = float(value)
        if not math.isfinite(value):
            raise ValidationError(f"{field} must be a finite number.")
        return value

    # int
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise ValidationError(f"{field} must be a whole number.") from None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int):
        return value
    raise ValidationError(f"{field} must be a whole number.")


def clean_filter(query: Any, where: str = "query") -> Dict[str, Any]:
    """Validate a read/update/delete filter: whitelisted fields, plain values."""
    if query is None:
        return {}
    if not isinstance(query, Mapping):
        raise ValidationError(f"{where} must be a dictionary.")
    reject_operator_keys(query, where)
    cleaned: Dict[str, Any] = {}
    for field, value in query.items():
        if field not in ALLOWED_FIELDS:
            raise ValidationError(f"{field!r} is not a field you can filter on.")
        cleaned[field] = coerce_value(field, value)
    return cleaned


def clean_document(data: Any, where: str = "document") -> Dict[str, Any]:
    """Validate a document for insert or $set: whitelisted fields, coerced values."""
    if data is None or not isinstance(data, Mapping):
        raise ValidationError(f"{where} must be a non-empty dictionary.")
    if not data:
        raise ValidationError(f"{where} is empty; nothing to save.")
    reject_operator_keys(data, where)
    cleaned: Dict[str, Any] = {}
    for field, value in data.items():
        if field == "_id" or field in DERIVED_FIELDS:
            raise ValidationError(f"{field!r} is managed by the database layer and cannot be set.")
        if field not in ALLOWED_FIELDS:
            raise ValidationError(f"{field!r} is not a known animal field.")
        cleaned[field] = coerce_value(field, value)
    return cleaned


def geo_point(lat: float, lon: float) -> Dict[str, Any]:
    """GeoJSON point. Note the order: [longitude, latitude]."""
    return {"type": "Point", "coordinates": [float(lon), float(lat)]}


def build_profile_pipeline(
    profile: Optional[str],
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Pipeline for the table: $match -> $sort -> [$skip -> $limit] -> $project.

    With page/page_size None the pipeline returns every match (used by the
    explain report so nReturned is the whole profile, not one page).
    """
    pipeline: List[Dict[str, Any]] = [{"$match": match_criteria(profile)}, {"$sort": dict(PROFILE_SORT)}]
    if page is not None or page_size is not None:
        page = _require_int(page, "page", 0, MAX_PAGE)
        page_size = _require_int(page_size, "page_size", 1, MAX_PAGE_SIZE)
        pipeline += [{"$skip": page * page_size}, {"$limit": page_size}]
    pipeline.append({"$project": dict(TABLE_PROJECTION)})
    return pipeline


def build_breed_pipeline(profile: Optional[str], top: Optional[int] = None) -> List[Dict[str, Any]]:
    """$match -> $group by breed -> $sort by count. Counting happens in the database."""
    pipeline: List[Dict[str, Any]] = [
        {"$match": match_criteria(profile)},
        {"$group": {"_id": "$breed", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
    ]
    if top is not None:
        pipeline.append({"$limit": _require_int(top, "top", 1, 1000)})
    pipeline.append({"$project": {"_id": 0, "breed": "$_id", "count": 1}})
    return pipeline


# ---------------------------------------------------------------------------
# Driver error translation
# ---------------------------------------------------------------------------

@contextmanager
def _db_errors(action: str) -> Iterator[None]:
    """Turn pymongo exceptions into typed errors with safe messages.

    The full driver message is logged server-side; the raised error only says
    what failed, so hostnames, query internals, and stack traces never reach
    the browser.
    """
    try:
        yield
    except ShelterError:
        raise
    except pymongo_errors.ConnectionFailure as exc:  # includes ServerSelectionTimeoutError
        logger.warning("%s: database unreachable (%s): %s", action, type(exc).__name__, exc)
        raise DatabaseUnavailable(f"Could not {action}: the database is not reachable right now.") from None
    except pymongo_errors.OperationFailure as exc:  # includes WriteError
        code = getattr(exc, "code", None)
        logger.warning("%s: operation failed (code %s): %s", action, code, exc)
        if code == _DOC_VALIDATION_FAILED:
            raise ValidationError(f"Could not {action}: the document failed schema validation.") from None
        if code == _UNAUTHORIZED:
            raise PermissionDenied(f"Could not {action}: this database user is not allowed to do that.") from None
        if code == _AUTH_FAILED:
            raise ConfigurationError("The database rejected the credentials in MONGO_URI.") from None
        raise ShelterError(f"Could not {action}.") from None
    except pymongo_errors.ConfigurationError as exc:
        logger.warning("%s: driver configuration error: %s", action, exc)
        raise ConfigurationError("MONGO_URI could not be used. Check the connection string.") from None
    except pymongo_errors.PyMongoError as exc:
        logger.warning("%s: driver error (%s): %s", action, type(exc).__name__, exc)
        raise ShelterError(f"Could not {action}.") from None


# ---------------------------------------------------------------------------
# The class
# ---------------------------------------------------------------------------

class AnimalShelter:
    """CRUD and query operations for the animals collection.

    Use as a context manager so the client is always closed:

        with AnimalShelter.from_env() as shelter:
            rows = shelter.query_by_profile("water", page=0, page_size=10)

    Return values match the CS 340 contract: create -> True, read -> list,
    update -> modified count, delete -> deleted count.
    """

    def __init__(self, settings: Settings, client: Optional[Any] = None) -> None:
        self.settings = settings
        if client is None:
            with _db_errors("connect"):
                client = MongoClient(
                    settings.uri,
                    serverSelectionTimeoutMS=settings.timeout_ms,
                    connectTimeoutMS=settings.timeout_ms,
                    socketTimeoutMS=max(settings.timeout_ms * 4, 20000),
                    appname="aac-shelter-dashboard",
                )
        # The instance owns the client from here on and closes it in close().
        self.client = client
        self.collection = client[settings.db_name][settings.collection_name]
        self._closed = False

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "AnimalShelter":
        """Build from MONGO_URI and friends. Raises ConfigurationError if unset."""
        return cls(load_settings(environ))

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the client. Safe to call more than once."""
        if not self._closed:
            self._closed = True
            self.client.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "AnimalShelter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise ShelterError("This AnimalShelter has been closed.")

    def ping(self) -> bool:
        """Round-trip to the server. Raises DatabaseUnavailable if unreachable."""
        self._check_open()
        with _db_errors("reach the database"):
            self.client.admin.command("ping")
        return True

    # -- CRUD --------------------------------------------------------------

    def create(self, data: Mapping[str, Any]) -> bool:
        """Insert one animal. Returns True when acknowledged."""
        self._check_open()
        doc = clean_document(data, "create data")
        if "location_lat" in doc and "location_long" in doc:
            doc["location"] = geo_point(doc["location_lat"], doc["location_long"])
        with _db_errors("save the animal"):
            result = self.collection.insert_one(doc)
        return bool(result.acknowledged)

    def read(self, query: Optional[Mapping[str, Any]] = None, limit: int = DEFAULT_READ_LIMIT) -> List[Dict[str, Any]]:
        """Equality lookup on whitelisted fields. Always capped by limit."""
        self._check_open()
        flt = clean_filter(query, "read query")
        limit = _require_int(limit, "limit", 1, MAX_READ_LIMIT)
        with _db_errors("read animals"):
            return list(self.collection.find(flt, {"_id": 0, "location": 0}).limit(limit))

    def update(self, query: Mapping[str, Any], new_values: Mapping[str, Any]) -> int:
        """$set new_values on every match. Refuses an empty filter."""
        self._check_open()
        flt = clean_filter(query, "update query")
        if not flt:
            raise ValidationError("Refusing to update with an empty filter; it would change every animal.")
        changes = clean_document(new_values, "update values")
        has_lat, has_long = "location_lat" in changes, "location_long" in changes
        if has_lat != has_long:
            raise ValidationError("Update location_lat and location_long together.")
        if has_lat:
            changes["location"] = geo_point(changes["location_lat"], changes["location_long"])
        with _db_errors("update animals"):
            result = self.collection.update_many(flt, {"$set": changes})
        return int(result.modified_count)

    def delete(self, query: Mapping[str, Any]) -> int:
        """Delete every match. Refuses an empty filter."""
        self._check_open()
        flt = clean_filter(query, "delete query")
        if not flt:
            raise ValidationError("Refusing to delete with an empty filter; it would remove every animal.")
        with _db_errors("delete animals"):
            result = self.collection.delete_many(flt)
        return int(result.deleted_count)

    # -- dashboard queries --------------------------------------------------

    def query_by_profile(self, profile: Optional[str], page: int = 0, page_size: int = 10) -> List[Dict[str, Any]]:
        """One page of animals matching a rescue profile ("all" for no filter)."""
        self._check_open()
        pipeline = build_profile_pipeline(profile, page, page_size)
        with _db_errors("load animals"):
            return list(self.collection.aggregate(pipeline))

    def count_by_profile(self, profile: Optional[str]) -> int:
        """Total matches for a profile, for the table's page count."""
        self._check_open()
        criteria = match_criteria(profile)
        with _db_errors("count animals"):
            return int(self.collection.count_documents(criteria))

    def breed_counts(self, profile: Optional[str], top: Optional[int] = None) -> List[Dict[str, Any]]:
        """[{"breed": ..., "count": ...}] for a profile, largest first."""
        self._check_open()
        pipeline = build_breed_pipeline(profile, top)
        with _db_errors("count breeds"):
            return list(self.collection.aggregate(pipeline))

    def animals_near(self, latitude: Any, longitude: Any, max_meters: Any = 5000, limit: int = 25) -> List[Dict[str, Any]]:
        """Animals within max_meters of a point, nearest first. Uses the 2dsphere index."""
        self._check_open()
        lat = coerce_value("location_lat", latitude)
        lon = coerce_value("location_long", longitude)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValidationError("Coordinates are out of range.")
        try:
            meters = float(max_meters)
        except (TypeError, ValueError):
            raise ValidationError("max_meters must be a number.") from None
        if not (0 < meters <= 100_000) or isinstance(max_meters, bool):
            raise ValidationError("max_meters must be between 0 and 100000.")
        limit = _require_int(limit, "limit", 1, MAX_PAGE_SIZE)
        flt = {"location": {"$near": {"$geometry": geo_point(lat, lon), "$maxDistance": meters}}}
        with _db_errors("find nearby animals"):
            return list(self.collection.find(flt, dict(TABLE_PROJECTION)).limit(limit))
