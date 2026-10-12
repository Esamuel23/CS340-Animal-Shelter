# =============================================================================
# File:     profiles.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Server-owned Grazioso Salvare rescue criteria. The UI only sends a
#           profile key; the query is built here, so a browser can never
#           choose a field or an operator.
# =============================================================================
"""Rescue profile whitelist.

Criteria come from the Grazioso Salvare specification for CS 340 Project Two.
The CS 340 original filtered each button on a single breed and ignored sex and
age; these definitions restore the full spec (breed list, preferred sex, and
training age in weeks, inclusive on both ends).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .errors import ValidationError


@dataclass(frozen=True)
class RescueProfile:
    label: str
    breeds: Tuple[str, ...]
    sex: str
    min_weeks: float
    max_weeks: float


PROFILE_DEFINITIONS: Dict[str, RescueProfile] = {
    "water": RescueProfile(
        label="Water Rescue",
        breeds=("Labrador Retriever Mix", "Chesapeake Bay Retriever", "Newfoundland"),
        sex="Intact Female",
        min_weeks=26,
        max_weeks=156,
    ),
    "mountain": RescueProfile(
        label="Mountain or Wilderness Rescue",
        breeds=(
            "German Shepherd",
            "Alaskan Malamute",
            "Old English Sheepdog",
            "Siberian Husky",
            "Rottweiler",
        ),
        sex="Intact Male",
        min_weeks=26,
        max_weeks=156,
    ),
    "disaster": RescueProfile(
        label="Disaster Rescue or Individual Tracking",
        breeds=(
            "Doberman Pinscher",
            "German Shepherd",
            "Golden Retriever",
            "Bloodhound",
            "Rottweiler",
        ),
        sex="Intact Male",
        min_weeks=20,
        max_weeks=300,
    ),
}

# "all" is the reset option: no filter, still paged and still server-owned.
ALL_ANIMALS = "all"
VALID_PROFILE_KEYS = (ALL_ANIMALS, *PROFILE_DEFINITIONS.keys())


def get_profile(key: object) -> Optional[RescueProfile]:
    """Return the profile for key, None for "all", or raise ValidationError."""
    if key is None or key == ALL_ANIMALS:
        return None
    if not isinstance(key, str) or key not in PROFILE_DEFINITIONS:
        raise ValidationError(f"Unknown rescue profile. Choose one of: {', '.join(VALID_PROFILE_KEYS)}.")
    return PROFILE_DEFINITIONS[key]


def match_criteria(key: object) -> dict:
    """Build the $match document for a profile key. "all" or None returns {}."""
    profile = get_profile(key)
    if profile is None:
        return {}
    # Field order mirrors the compound index: equality fields, then the range field.
    return {
        "sex_upon_outcome": profile.sex,
        "breed": {"$in": list(profile.breeds)},
        "age_upon_outcome_in_weeks": {"$gte": profile.min_weeks, "$lte": profile.max_weeks},
    }
