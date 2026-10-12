# Enhancement Three Spec: Databases (Milestone Four)

Planning doc only. No implementation code. The Python is yours to write.
Milestone Four wants: the enhancement, a narrative (Word), and a zip of original + enhanced code.
Source of truth for scope: Module One plan, Category Three, and the code review (Artifact Two).

---

## 0. The whole map (read this first)

```
TODAY (CS 340)                                AFTER (Enhancement Three)
--------------                                -------------------------
Notebook (jupyter_dash)                       app.py (plain Dash), runs anywhere
  └─ AnimalShelter(user, pass)                  └─ AnimalShelter.from_env()  ← MONGO_URI only
       └─ hardcoded Apporto host                     └─ MongoDB Atlas, least-privilege user, IP allow list
       └─ find(query) → ALL docs → pandas            └─ aggregate([$match → $sort → $skip/$limit → $project])
       └─ no indexes (COLLSCAN)                      └─ compound + 2dsphere indexes (IXSCAN), explain() proof
       └─ query = whatever UI sends                  └─ whitelist of profile names → server-owned criteria
       └─ no schema                                  └─ $jsonSchema validator rejects bad writes
       └─ raw driver exceptions                      └─ typed errors, timeouts, close()/context manager
```

| Strand | Outcome | Priority for Milestone Four |
|---|---|---|
| A. Migration to Atlas + env config | 4, 5 | **Must** |
| B. Indexes + aggregation + explain() before/after | 3, 4 | **Must** (this is the measured evidence) |
| C. Hardening: whitelist, schema validator, errors, timeouts | 5 | **Must** |
| D. Repo hygiene: requirements.txt, app.py, tests, README | 2, 4 | **Must** |
| E. Employer portal (sessions + visits analytics) | 4, 5 | **Stretch** — defer to final if time is short; say so in the narrative |

If A–D are done and measured, Milestone Four passes. E is the "wow" piece for the final ePortfolio.

---

## 1. Target file layout

| File | What it is |
|---|---|
| `original/animal_shelter.py`, `original/dashboard.ipynb` | Untouched CS 340 copies (baseline for the zip and the diff) |
| `shelter/config.py` | Reads `MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`, timeout; fails fast if missing |
| `shelter/animal_shelter.py` | CRUD + query layer. Owns every DB call. Context manager. |
| `shelter/profiles.py` | `PROFILE_DEFINITIONS` — server-owned rescue criteria (the whitelist) |
| `shelter/schema.py` | The `$jsonSchema` document for the `animals` collection |
| `scripts/setup_db.py` | One-time: import CSV, create indexes, apply validator (idempotent) |
| `scripts/explain_report.py` | Runs each profile query, prints plan stage + docs examined; writes `docs/explain/*.json` |
| `app.py` | Plain Dash app (no `jupyter_dash`); calls only the shelter module |
| `tests/` | pytest; unit tests use `mongomock` or a test DB, never production |
| `requirements.txt` | Pinned versions |
| `.env.example` | Variable names only, no values. `.env` stays in `.gitignore` |
| `docs/THREAT_MODEL.md` | Short: what's protected, from whom, what isn't (required if you do strand E) |

---

## 2. Strand A — Atlas migration

Checklist (do in Atlas UI, screenshot each for the narrative):
1. Free M0 cluster.
2. Database user `shelter_app` with a **custom role**: `find`, `insert`, `update`, `remove` on `aac.animals` (and `aac.visits` if E) — nothing else. Not `readWriteAnyDatabase`, not admin.
3. Network access: your IP only (no `0.0.0.0/0`). Note it in the README as a deploy step.
4. Import the dataset (see Trouble spots — you need the CS 340 CSV with `location_lat` / `location_long`).
5. Connection string → `MONGO_URI` env var. Never in source, never in the notebook, never in a commit.

Config rules:
- Missing `MONGO_URI` → raise a clear configuration error at startup, not a driver stack trace.
- Set `serverSelectionTimeoutMS` (e.g. 5000) so a bad network fails in seconds, not 30.
- One client per process, closed on shutdown (`close()` + `__enter__/__exit__`).

---

## 3. Strand B — Query design (the measured part)

### Rescue profiles (server-owned)

Current code filters on **one breed per profile** — that doesn't match the Grazioso Salvare spec.
Re-implement from the CS 340 spec document (verify against your copy; from memory it is roughly):

| Profile | Breeds | Sex | Age (weeks) |
|---|---|---|---|
| Water | Labrador Retriever Mix, Chesapeake Bay Retriever, Newfoundland | Intact Female | 26–156 |
| Mountain/Wilderness | German Shepherd, Alaskan Malamute, Old English Sheepdog, Siberian Husky, Rottweiler | Intact Male | 26–156 |
| Disaster/Tracking | Doberman Pinscher, German Shepherd, Golden Retriever, Bloodhound, Rottweiler | Intact Male | 20–300 |

Fixing this is itself a correctness win — call it out in the narrative.

### Query path (pseudocode, same as Module One)

```
query_by_profile(profile, page, page_size):
    if profile not in PROFILE_DEFINITIONS: raise ValidationError
    if page < 0 or page_size not in 1..100: raise ValidationError
    criteria = PROFILE_DEFINITIONS[profile]          // $in breeds, exact sex, $gte/$lte age
    pipeline = [ $match criteria,
                 $sort  {age_upon_outcome_in_weeks: 1},
                 $skip  page*page_size, $limit page_size,
                 $project {only the columns the table shows, _id: 0} ]
    return list(collection.aggregate(pipeline))

breed_counts(profile):   // for the pie chart — counting moves into the DB
    [ $match criteria, $group {_id: "$breed", count: {$sum: 1}}, $sort {count: -1} ]
```

### Indexes (create in `setup_db.py`, idempotent)

| Index | Serves | Why this order |
|---|---|---|
| `{sex_upon_outcome: 1, breed: 1, age_upon_outcome_in_weeks: 1}` | every profile `$match` + the sort | Equality fields first, range/sort field last (ESR rule) |
| `{location: "2dsphere"}` | map view / any near-me query | Requires a GeoJSON `location` field — build it from lat/long during import |

ESR = Equality, Sort, Range. This is the trade-off paragraph: an index speeds reads but costs write time and storage — measure index size (`collStats`) and say it.

### explain() evidence — Outcome 3

For each profile, run `explain("executionStats")` **before** creating indexes and **after**:

| Capture | Where in the explain output |
|---|---|
| Plan stage | `queryPlanner.winningPlan` → expect `COLLSCAN` before, `IXSCAN` after |
| Docs examined | `executionStats.totalDocsExamined` |
| Keys examined | `executionStats.totalKeysExamined` |
| Docs returned | `executionStats.nReturned` |
| Time | `executionStats.executionTimeMillis` (noisy on M0 — report median of 5) |

Save raw JSON to `docs/explain/` and build Table 1 for the narrative:
`profile | stage before/after | docs examined before/after | returned | ms before/after`.
Headline metric: **docs examined ÷ docs returned** — ~collection size before, ~1 after.

---

## 4. Strand C — Hardening (Outcome 5)

| Gap (code review) | Fix | Test |
|---|---|---|
| UI value goes straight into a query | Whitelist lookup only; UI never supplies a field or operator | Unknown profile → ValidationError |
| `read(query)` accepts any dict (operator injection: `$where`, `$ne`, `$regex`) | Generic `read` validates keys against an allowed field set and **rejects any key starting with `$`** at any depth; values type-checked | `{"$where": "..."}` rejected; `{"breed": {"$ne": None}}` rejected |
| No schema | `$jsonSchema` validator: required fields, types, `age_upon_outcome_in_weeks` ≥ 0, `sex_upon_outcome` enum, lat/long ranges. `validationAction: "error"` | Bad insert raises your typed error |
| Raw `Exception(...)` everywhere | Small error hierarchy: `ConfigurationError`, `ValidationError`, `DatabaseUnavailable` | Each raised in the right place |
| `create(None)` vs `create({})` | Reject empty dicts too | |
| `update`/`delete` with `{}` would hit **every document** | Refuse empty filters on update/delete | `delete({})` rejected |
| No timeout, no teardown | Timeout in config; context manager | Unreachable host fails < 10 s |
| Credentials | Env only; `.env.example`; scan history once (`git log -p | findstr /i pass`) | — |

The `update/delete({})` one is a great narrative detail: the original would have wiped the collection.

---

## 5. Strand D — Hygiene

- `requirements.txt` pinned: pymongo, dash, dash-leaflet, plotly, pandas, python-dotenv (dev: pytest, mongomock).
- `app.py` replaces the notebook. Fix the code-review items while porting: the broken import, the missing logo file, `None` tested last, FIX ME leftovers, map index bounds check when the filtered table is empty.
- Table uses server-side paging (`page_action="custom"`) so the browser never loads the whole collection.
- README: setup in 5 steps, env vars, how to run `setup_db.py`, `explain_report.py`, tests; before/after table.
- Every new file: header with name, date, purpose (match the ray tracer headers).

---

## 6. Strand E — Employer portal (stretch)

Only if A–D are done. Pseudocode is in the Module One plan. Minimum honest version:
- Dash runs on Flask: protect routes with a Flask `before_request` that checks a server-side session; unauthenticated → sign-in page, no data in the response.
- Sign-in form: name + company, sanitized, length-limited; cookie `HttpOnly`, `Secure`, `SameSite=Lax`; 2-hour expiry; simple per-IP rate limit.
- `visits` collection + index `{company: 1, timestamp: -1}`; `$group` pipeline for views per company, first/last seen.
- `docs/THREAT_MODEL.md`: self-asserted company ≠ authentication. Say it plainly.

If you skip it: one sentence in the narrative — "deferred to the final ePortfolio; strands A–D are complete and measured."

---

## 7. Tests (pytest)

| # | Test | Proves |
|---|---|---|
| 1 | Missing `MONGO_URI` → ConfigurationError | fail fast |
| 2 | Unknown profile → ValidationError | whitelist |
| 3 | `$`-prefixed key (top level and nested) rejected by `read` | injection |
| 4 | `update({}, …)` and `delete({})` rejected | no mass writes |
| 5 | `create({})` / `create(None)` rejected | input |
| 6 | Each profile returns only matching breed/sex/age (seeded fixture data) | correctness |
| 7 | Paging: page 0 and page 1 don't overlap; page_size bounds enforced | paging |
| 8 | `breed_counts` sums to the profile's total | aggregation |
| 9 | Validator rejects a doc with negative age (integration, test DB only) | schema |
| 10 | Context manager closes the client | teardown |

Mark integration tests (need Atlas/test DB) with a pytest marker so unit tests run anywhere.

---

## 8. Done when

- [ ] App runs from a fresh clone with only `MONGO_URI` set
- [ ] `setup_db.py` creates indexes + validator; rerunning is harmless
- [ ] explain() before/after JSON in `docs/explain/`, Table 1 filled
- [ ] All unit tests pass
- [ ] No credential anywhere in the repo or its history
- [ ] README + headers done
- [ ] Narrative placeholders filled
- [ ] Zip = `original/` + enhanced code (no `.venv`, no `__pycache__`, no `.env`)

## 9. Fallbacks if time gets tight

1. Drop E (portal) → final ePortfolio.
2. Drop 2dsphere → keep lat/long as plain fields; keep the compound index (that's the core evidence).
3. Drop server-side paging → keep `$limit`; mention it.
Never drop: env-only credentials, the whitelist, explain() before/after.

## 10. Trouble spots

- **Dataset**: the CS 340 CSV (`aac_shelter_outcomes.csv`, ~10k rows with `location_lat`/`location_long`) isn't on this machine. Pull it from the CS 340 course resources / Apporto export. The public Austin Animal Center data lacks the lat/long columns.
- **M0 timings are noisy** — lead with docs examined, not milliseconds.
- **Atlas free tier limits** some admin commands; `explain` and `collStats` work.

## References for the narrative

- MongoDB, Inc. (n.d.). *The ESR (equality, sort, range) guideline*. MongoDB Manual. https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-guideline/
- MongoDB, Inc. (n.d.). *Explain results*. MongoDB Manual. https://www.mongodb.com/docs/manual/reference/explain-results/
- MongoDB, Inc. (n.d.). *Schema validation*. MongoDB Manual. https://www.mongodb.com/docs/manual/core/schema-validation/
- OWASP Foundation. (n.d.). *Testing for NoSQL injection*. OWASP Web Security Testing Guide. https://wstg.owasp.org/v4.2/4-Web_Application_Security_Testing/07-Input_Validation_Testing/05.6-Testing_for_NoSQL_Injection/
