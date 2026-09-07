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
