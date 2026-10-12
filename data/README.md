# Data: `aac_shelter_outcomes.csv`

## What it is

The CS 340 course dataset: 10,000 Austin Animal Center outcome records with these columns:

`(unnamed row number), age_upon_outcome, animal_id, animal_type, breed, color, date_of_birth, datetime,
monthyear, name, outcome_subtype, outcome_type, sex_upon_outcome, location_lat, location_long,
age_upon_outcome_in_weeks`

`scripts/setup_db.py` stores the unnamed first column as `rec_num` and builds a GeoJSON
`location` field from `location_lat` / `location_long`.

## Where this copy came from

The original course file was not in this repo or on this machine, so a public copy was used.

- Downloaded from: https://github.com/morgval/GS-Dashboard (file `aac_shelter_outcomes.csv`, repo's
  last commit 2023-04-19)
- SHA-256: `464639d961524a5a59d504654315b5b3e310524dbe8e5a91fb84479025c2973a`
- 10,001 lines (header + 10,000 rows), 16 columns in every row.

The same file, byte for byte (same SHA-256), is also in three other public CS 340 student repos:
`IMETALH/CS-340-Portfolio`, `DKozzy/CS-340-Enhanced`, and `vhicktour/Snhu-CS340`
(`module 5/m5 files/`). `eslutz/CS-340-Portfolio` has the same 10,000 data rows with a different
header label on the first column. Four identical copies from different students is the evidence
that this is the file SNHU distributes for the course.

## License and terms

- The records come from the City of Austin's open data portal (Austin Animal Center Outcomes,
  https://data.austintexas.gov/ — also listed at https://catalog.data.gov/dataset/austin-animal-center-outcomes).
  The City publishes that dataset as open data for public use.
- The `location_lat` / `location_long` columns are NOT in the public City of Austin dataset. They
  come with the SNHU course file, and the course does not document how they were produced. Treat
  them as course-provided teaching coordinates, not real addresses.
- None of the GitHub repos above has a LICENSE file. The file is redistributed here only as course
  material for an academic project. If that is a concern for a public repo, delete `data/` and
  point `setup_db.py --csv` at your own copy from the course.

## Known data issue

One row fails the schema validator and is not imported:

| rec_num | animal_id | Problem |
|---|---|---|
| 8055 | A749253 | `date_of_birth` 2017-07-01 is after the outcome date 2017-05-12, so `age_upon_outcome_in_weeks` is -7.04 |

The row is reported in `docs/import_report.json`. It is not "fixed" because there is no way to
know the right date. The database therefore holds **9,999** documents.
