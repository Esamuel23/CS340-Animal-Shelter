# =============================================================================
# File:     setup_db.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  One-time database setup: apply the $jsonSchema validator, import
#           the AAC CSV (building a GeoJSON location from lat/long), and
#           create the compound ESR index and the 2dsphere index.
#           Idempotent: rerunning changes nothing that is already in place.
# =============================================================================
"""Set up aac.animals.

Usage (from the repo root):
    python scripts/setup_db.py                 # validator + import-if-empty + indexes
    python scripts/setup_db.py --reload        # drop and re-import the data
    python scripts/setup_db.py --no-indexes    # skip index creation

Connects with MONGO_SETUP_URI if set (an account with readWrite + dbAdmin on
the database), otherwise MONGO_URI. The app's own user cannot run this, by
design: it has no createIndex or collMod rights.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from pymongo import MongoClient  # noqa: E402
from pymongo.errors import BulkWriteError, CollectionInvalid  # noqa: E402

from shelter.config import load_setup_settings, redact_uri  # noqa: E402
from shelter.errors import ShelterError  # noqa: E402
from shelter.schema import (  # noqa: E402
    ANIMAL_INDEXES,
    FLOAT_FIELDS,
    STRING_FIELDS,
    VALIDATION_ACTION,
    VALIDATION_LEVEL,
    VALIDATOR,
)

DEFAULT_CSV = ROOT / "data" / "aac_shelter_outcomes.csv"
IMPORT_REPORT = ROOT / "docs" / "import_report.json"
BATCH = 1000


def row_to_document(row: Dict[str, str]) -> Dict[str, Any]:
    """Convert one CSV row to a document. Values are not changed or invented.

    The CSV's unnamed first column is the course's record number -> rec_num.
    Empty numeric cells are left out rather than guessed.
    """
    doc: Dict[str, Any] = {}
    rec = (row.get("") or row.get("1") or "").strip()
    if rec.isdigit():
        doc["rec_num"] = int(rec)
    for field in STRING_FIELDS:
        if field in row:
            doc[field] = row[field] if row[field] is not None else ""
    for field in FLOAT_FIELDS:
        raw = (row.get(field) or "").strip()
        if raw:
            doc[field] = float(raw)
    if "location_lat" in doc and "location_long" in doc:
        doc["location"] = {"type": "Point", "coordinates": [doc["location_long"], doc["location_lat"]]}
    return doc


def ensure_validator(db, name: str) -> str:
    """Create the collection with the validator, or update it on an existing one."""
    try:
        db.create_collection(
            name,
            validator=VALIDATOR,
            validationLevel=VALIDATION_LEVEL,
            validationAction=VALIDATION_ACTION,
        )
        return "created collection with validator"
    except CollectionInvalid:
        db.command(
            "collMod",
            name,
            validator=VALIDATOR,
            validationLevel=VALIDATION_LEVEL,
            validationAction=VALIDATION_ACTION,
        )
        return "validator applied to existing collection (collMod)"


def import_csv(collection, csv_path: Path) -> Dict[str, Any]:
    """Insert every row. Rows the validator rejects are reported, not altered."""
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    docs = [row_to_document(r) for r in rows]
    inserted = 0
    rejected: List[Dict[str, Any]] = []
    for start in range(0, len(docs), BATCH):
        batch = docs[start:start + BATCH]
        try:
            result = collection.insert_many(batch, ordered=False)
            inserted += len(result.inserted_ids)
        except BulkWriteError as exc:
            details = exc.details
            inserted += details.get("nInserted", 0)
            for err in details.get("writeErrors", []):
                bad = batch[err["index"]]
                rejected.append({
                    "rec_num": bad.get("rec_num"),
                    "animal_id": bad.get("animal_id"),
                    "code": err.get("code"),
                    "reason": _short_reason(err),
                })
    return {"csv_rows": len(rows), "inserted": inserted, "rejected": rejected}


def _short_reason(err: Dict[str, Any]) -> str:
    """Pull the failing property names out of a DocumentValidationFailure."""
    if err.get("code") != 121:
        return err.get("errmsg", "write error")[:200]
    names: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("propertyName", "missingProperties"):
                if key in node:
                    val = node[key]
                    names.extend(val if isinstance(val, list) else [val])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(err.get("errInfo", {}))
    return "schema validation failed on: " + (", ".join(dict.fromkeys(names)) or "unknown field")


def ensure_indexes(collection) -> List[Tuple[str, str]]:
    """create_index is a no-op when an identical index already exists."""
    out = []
    for name, keys in ANIMAL_INDEXES:
        out.append((name, collection.create_index(keys, name=name)))
    return out


def ensure_visits_index(db) -> Optional[str]:
    """Index for the employer portal's visits collection (strand E)."""
    return db["visits"].create_index([("company", 1), ("timestamp", -1)], name="company_timestamp")


def ensure_session_index(db) -> Optional[str]:
    """TTL index: MongoDB deletes portal sessions once expires_at passes."""
    return db["portal_sessions"].create_index("expires_at", name="session_ttl", expireAfterSeconds=0)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="path to aac_shelter_outcomes.csv")
    parser.add_argument("--reload", action="store_true", help="drop the collection and import again")
    parser.add_argument("--no-indexes", action="store_true", help="skip index creation")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    try:
        settings = load_setup_settings()
    except ShelterError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"Connecting to {redact_uri(settings.uri)} (db={settings.db_name}, collection={settings.collection_name})")
    with MongoClient(settings.uri, serverSelectionTimeoutMS=settings.timeout_ms) as client:
        db = client[settings.db_name]
        coll = db[settings.collection_name]

        if args.reload and settings.collection_name in db.list_collection_names():
            coll.drop()
            print("dropped existing collection (--reload)")

        # Validator first, so the import itself is checked by the schema.
        print(ensure_validator(db, settings.collection_name))

        existing = coll.estimated_document_count()
        if existing == 0:
            if not args.csv.exists():
                print(f"CSV not found: {args.csv}", file=sys.stderr)
                return 1
            report = import_csv(coll, args.csv)
            print(f"imported {report['inserted']} of {report['csv_rows']} rows; rejected {len(report['rejected'])}")
            for bad in report["rejected"]:
                print(f"  rejected rec_num={bad['rec_num']} animal_id={bad['animal_id']}: {bad['reason']}")
            IMPORT_REPORT.parent.mkdir(parents=True, exist_ok=True)
            IMPORT_REPORT.write_text(json.dumps({"csv": args.csv.name, **report}, indent=2) + "\n")
        else:
            print(f"collection already has {existing} documents; import skipped (use --reload to re-import)")

        if not args.no_indexes:
            for name, result in ensure_indexes(coll):
                print(f"index ready: {name} ({result})")
            print(f"index ready: {ensure_visits_index(db)} on visits")
            print(f"index ready: {ensure_session_index(db)} on portal_sessions")

        print(f"documents: {coll.count_documents({})}")
        print(f"indexes on {settings.collection_name}: {sorted(coll.index_information())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
