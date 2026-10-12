# =============================================================================
# File:     create_local_users.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  For a LOCAL mongod started with --auth: creates the two
#           least-privilege accounts the project uses. On Atlas you do the
#           same thing in the web UI (see docs/ATLAS_SETUP.md).
# =============================================================================
"""Create the shelterApp role and the shelter_app / shelter_setup users.

Environment (never hard-code these):
  MONGO_ADMIN_URI         a root/userAdmin login to the local server
  SHELTER_APP_PASSWORD    password to give shelter_app
  SHELTER_SETUP_PASSWORD  password to give shelter_setup
  MONGO_DB                optional, default "aac"

shelter_app   custom role shelterApp: find/insert/update/remove on aac.animals,
              find/insert on aac.visits, find/insert/update/remove on
              aac.portal_sessions. Nothing else: it cannot drop collections,
              create indexes, change the validator, or read other databases.
shelter_setup built-in readWrite + dbAdmin on aac only, for setup_db.py and
              explain_report.py (index and validator changes).

Safe to rerun: existing role/users are updated in place.
"""

from __future__ import annotations

import os
import sys

from pymongo import MongoClient
from pymongo.errors import OperationFailure

APP_ROLE = "shelterApp"


def app_privileges(db: str) -> list:
    return [
        {"resource": {"db": db, "collection": "animals"}, "actions": ["find", "insert", "update", "remove"]},
        {"resource": {"db": db, "collection": "visits"}, "actions": ["find", "insert"]},
        {"resource": {"db": db, "collection": "portal_sessions"}, "actions": ["find", "insert", "update", "remove"]},
    ]


def upsert_role(database, name: str, privileges: list) -> None:
    try:
        database.command("createRole", name, privileges=privileges, roles=[])
        print(f"created role {name}")
    except OperationFailure as exc:
        if exc.code != 51002:  # RoleAlreadyExists
            raise
        database.command("updateRole", name, privileges=privileges, roles=[])
        print(f"updated role {name}")


def upsert_user(database, name: str, password: str, roles: list) -> None:
    try:
        database.command("createUser", name, pwd=password, roles=roles)
        print(f"created user {name}")
    except OperationFailure as exc:
        if exc.code != 51003:  # UserAlreadyExists
            raise
        database.command("updateUser", name, pwd=password, roles=roles)
        print(f"updated user {name}")


def main() -> int:
    missing = [k for k in ("MONGO_ADMIN_URI", "SHELTER_APP_PASSWORD", "SHELTER_SETUP_PASSWORD") if not os.environ.get(k)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    db_name = os.environ.get("MONGO_DB", "aac")
    with MongoClient(os.environ["MONGO_ADMIN_URI"], serverSelectionTimeoutMS=5000) as client:
        database = client[db_name]
        upsert_role(database, APP_ROLE, app_privileges(db_name))
        upsert_user(database, "shelter_app", os.environ["SHELTER_APP_PASSWORD"], [{"role": APP_ROLE, "db": db_name}])
        upsert_user(
            database,
            "shelter_setup",
            os.environ["SHELTER_SETUP_PASSWORD"],
            [{"role": "readWrite", "db": db_name}, {"role": "dbAdmin", "db": db_name}],
        )
    print("Done. Users authenticate against the", db_name, "database (authSource=" + db_name + ").")
    return 0


if __name__ == "__main__":
    sys.exit(main())
