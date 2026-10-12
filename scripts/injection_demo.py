# =============================================================================
# File:     injection_demo.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Shows, against the live collection, what an operator-injection
#           filter returns when passed straight to find() (the CS 340
#           behavior) versus through the enhanced AnimalShelter.read().
#           Read-only: nothing is written or deleted.
# =============================================================================
"""Usage: python scripts/injection_demo.py   (uses MONGO_URI, the app user)"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from shelter import AnimalShelter, ShelterError, ValidationError  # noqa: E402

ATTACKS = [
    ("$where JavaScript", {"$where": "true"}),
    ("$ne null on breed", {"breed": {"$ne": None}}),
    ("$regex match-all on name", {"name": {"$regex": ".*"}}),
    ("$gt on animal_id", {"animal_id": {"$gt": ""}}),
]


def main() -> int:
    load_dotenv(ROOT / ".env")
    try:
        shelter = AnimalShelter.from_env()
    except ShelterError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    with shelter:
        print(f"Collection has {shelter.collection.estimated_document_count():,} documents.\n")
        print(f"{'attack':28} | {'raw find() like CS 340':24} | enhanced read()")
        print("-" * 90)
        for label, query in ATTACKS:
            try:
                # Same call the CS 340 read() made: find(query), all matches.
                raw = f"{len(list(shelter.collection.find(query, {'_id': 1}))):,} documents"
            except Exception as exc:  # e.g. $where disabled server-side
                raw = f"server error: {type(exc).__name__}"
            try:
                shelter.read(query)
                enhanced = "ALLOWED (bug)"
            except ValidationError as exc:
                enhanced = f"ValidationError: {exc}"
            print(f"{label:28} | {raw:24} | {enhanced}")
        print("\nLegitimate equality lookup still works:",
              [r["animal_id"] for r in shelter.read({"breed": "Bloodhound"}, limit=5)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
