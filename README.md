# Animal Shelter Dashboard (CS-340)

A Python CRUD layer over MongoDB plus a Dash web dashboard that reads through it —
filter the shelter's animal records by rescue-type profile and see the matching
set as a table, a breed chart, and a map of where each animal is.

Coursework artifact from SNHU CS-340 (Client/Server Development), 2025.

## What's here

- `animal_shelter.py` — the `AnimalShelter` class: create / read / update / delete
  against the `AAC.animals` collection, with the connection built from credentials
  passed in rather than hardcoded.
- `dashboard.ipynb` — the Dash application: rescue-type filters (water, mountain,
  disaster), an interactive data table, a breed distribution chart, and a
  geolocation map that follows the selected row.
- `screenshots/` — the dashboard running against the course database.

## Credentials

The notebook reads them from the environment instead of carrying the classroom
values inline:

```bash
export MONGO_USER=...   # Windows: set MONGO_USER=...
export MONGO_PASS=...
```

`HOST` in `animal_shelter.py` still points at the SNHU Apporto lab instance the
assignment ran against, so this needs pointing at your own MongoDB to run outside
that environment.

## Notes

The CRUD layer is deliberately separate from the dashboard — the dashboard never
issues a query itself, it calls the module. That separation is the part of this
artifact worth looking at.
