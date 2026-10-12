# =============================================================================
# File:     visits_report.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Prints employer-portal engagement per company (a $group pipeline
#           over the visits collection). Run by the owner from a terminal;
#           the dashboard never shows one visitor another company's visits.
# =============================================================================
"""Usage: python scripts/visits_report.py [--company "Example Co"]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from shelter import AnimalShelter, ShelterError  # noqa: E402
from shelter.portal import EmployerPortal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", help="show recent visits for one company")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    try:
        with AnimalShelter.from_env() as shelter:
            portal = EmployerPortal(shelter.client[shelter.settings.db_name])
            if args.company:
                for v in portal.company_history(args.company):
                    print(f"{v['timestamp']:%Y-%m-%d %H:%M} UTC  {v['event']:6}  {v['name']}")
                return 0
            rows = portal.engagement()
    except ShelterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # driver errors from the portal's direct collection calls
        print(f"Error: could not read visits ({type(exc).__name__}).", file=sys.stderr)
        return 1
    if not rows:
        print("No visits yet.")
        return 0
    print(f"{'company':30} {'visitors':>8} {'views':>6} {'events':>6}  first seen (UTC)   last seen (UTC)")
    for r in rows:
        print(f"{r['company'][:30]:30} {r['visitors']:>8} {r['page_views']:>6} {r['events']:>6}  "
              f"{r['first_seen']:%Y-%m-%d %H:%M}   {r['last_seen']:%Y-%m-%d %H:%M}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
