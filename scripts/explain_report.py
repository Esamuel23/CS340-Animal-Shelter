# =============================================================================
# File:     explain_report.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Measures each rescue-profile query with explain("executionStats")
#           before and after the indexes exist. Writes the raw explain JSON
#           and a Markdown summary table to docs/explain/.
# =============================================================================
"""Before/after explain() evidence for the indexes.

Usage (from the repo root, after setup_db.py has imported the data):
    python scripts/explain_report.py

What it does:
  1. drops the profile_esr and location_2dsphere indexes (if present)
  2. explains each profile pipeline 5 times -> docs/explain/before_<name>.json
  3. recreates the indexes exactly as setup_db.py does
  4. explains again 5 times -> docs/explain/after_<name>.json
  5. records collection size and index sizes ($collStats) and index use ($indexStats)
  6. writes docs/explain/SUMMARY.md and docs/explain/summary.json

The pipeline explained is the one the dashboard runs ($match -> $sort ->
$project), without $skip/$limit, so nReturned is the whole profile.
Needs MONGO_SETUP_URI (dbAdmin) because it drops and creates indexes.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bson import json_util  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from pymongo import MongoClient  # noqa: E402
from pymongo.errors import OperationFailure  # noqa: E402

from shelter.animal_shelter import build_profile_pipeline  # noqa: E402
from shelter.config import load_setup_settings, redact_uri  # noqa: E402
from shelter.errors import ShelterError  # noqa: E402
from shelter.profiles import PROFILE_DEFINITIONS  # noqa: E402
from shelter.schema import ANIMAL_INDEXES  # noqa: E402

OUT = ROOT / "docs" / "explain"
RUNS = 5
GEO_RADIUS_KM = 2.0
EARTH_RADIUS_KM = 6378.1


def find_stats(explain: Dict[str, Any]) -> Dict[str, Any]:
    """Locate queryPlanner/executionStats in either explain shape.

    When the whole pipeline is pushed down to the query layer the sections are
    at the top level; otherwise they sit under stages[0]["$cursor"].
    """
    if "queryPlanner" in explain:
        return explain
    for stage in explain.get("stages", []):
        if "$cursor" in stage:
            return stage["$cursor"]
    raise ValueError("explain output had no queryPlanner section")


def plan_stages(node: Any) -> List[str]:
    """All stage names in a plan tree, outermost first."""
    found: List[str] = []
    if isinstance(node, dict):
        if "stage" in node:
            found.append(node["stage"])
        for key in ("queryPlan", "inputStage", "inputStages", "outerStage", "innerStage"):
            if key in node:
                found += plan_stages(node[key])
    elif isinstance(node, list):
        for child in node:
            found += plan_stages(child)
    return found


def headline_stage(stages: List[str]) -> str:
    if "COLLSCAN" in stages:
        return "COLLSCAN"
    if "IXSCAN" in stages:
        return "IXSCAN"
    return stages[-1] if stages else "?"


def run_explain(db, command: Dict[str, Any]) -> Dict[str, Any]:
    return db.command({"explain": command, "verbosity": "executionStats"})


def measure(db, name: str, command: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """Explain RUNS times; keep the last raw output; report the median time."""
    times: List[int] = []
    raw: Optional[Dict[str, Any]] = None
    for _ in range(RUNS):
        raw = run_explain(db, command)
        times.append(int(find_stats(raw)["executionStats"]["executionTimeMillis"]))
    assert raw is not None
    stats = find_stats(raw)
    es = stats["executionStats"]
    stages = plan_stages(stats["queryPlanner"]["winningPlan"])
    indexes_used = sorted({
        n.get("indexName") for n in _walk(stats["queryPlanner"]["winningPlan"]) if n.get("indexName")
    })
    (OUT / f"{phase}_{name}.json").write_text(json_util.dumps(raw, indent=2) + "\n")
    return {
        "stage": headline_stage(stages),
        "plan": " > ".join(stages),
        "indexes_used": indexes_used,
        "nReturned": es["nReturned"],
        "totalDocsExamined": es["totalDocsExamined"],
        "totalKeysExamined": es["totalKeysExamined"],
        "executionTimeMillis_runs": times,
        "executionTimeMillis_median": statistics.median(times),
    }


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def drop_indexes(coll) -> List[str]:
    existing = coll.index_information()
    dropped = []
    for name, _ in ANIMAL_INDEXES:
        if name in existing:
            coll.drop_index(name)
            dropped.append(name)
    return dropped


def create_indexes(coll) -> None:
    for name, keys in ANIMAL_INDEXES:
        coll.create_index(keys, name=name)


def storage_stats(coll) -> Dict[str, Any]:
    stats = next(coll.aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
    return {
        "count": stats["count"],
        "size_bytes": stats["size"],
        "avgObjSize_bytes": stats.get("avgObjSize"),
        "storageSize_bytes": stats["storageSize"],
        "totalIndexSize_bytes": stats["totalIndexSize"],
        "indexSizes_bytes": dict(stats["indexSizes"]),
    }


def index_usage(coll) -> Optional[Dict[str, int]]:
    """$indexStats needs the indexStats action (clusterMonitor), which the
    setup user deliberately does not have. Skip it rather than widen the role."""
    try:
        return {d["name"]: int(d["accesses"]["ops"]) for d in coll.aggregate([{"$indexStats": {}}])}
    except OperationFailure as exc:
        if exc.code == 13:
            return None
        raise


def main() -> int:
    load_dotenv(ROOT / ".env")
    try:
        settings = load_setup_settings()
    except ShelterError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Connecting to {redact_uri(settings.uri)}")
    with MongoClient(settings.uri, serverSelectionTimeoutMS=settings.timeout_ms) as client:
        db = client[settings.db_name]
        coll = db[settings.collection_name]
        doc_count = coll.count_documents({})
        if doc_count == 0:
            print("Collection is empty. Run scripts/setup_db.py first.", file=sys.stderr)
            return 1
        server_version = client.server_info()["version"]

        commands: Dict[str, Dict[str, Any]] = {
            name: {"aggregate": coll.name, "pipeline": build_profile_pipeline(name), "cursor": {}}
            for name in PROFILE_DEFINITIONS
        }
        # Geo query around a real record's own coordinates (the lowest rec_num), not an invented point.
        anchor = coll.find_one({"location": {"$exists": True}}, {"location": 1, "animal_id": 1}, sort=[("rec_num", 1)])
        geo_filter = None
        if anchor:
            lon, lat = anchor["location"]["coordinates"]
            geo_filter = {"location": {"$geoWithin": {"$centerSphere": [[lon, lat], GEO_RADIUS_KM / EARTH_RADIUS_KM]}}}
            commands["geo_within_2km"] = {"find": coll.name, "filter": geo_filter, "projection": {"_id": 0, "animal_id": 1}}

        dropped = drop_indexes(coll)
        print(f"dropped indexes for the 'before' run: {dropped or 'none present'}")
        before = {name: measure(db, name, cmd, "before") for name, cmd in commands.items()}

        near_before = None
        if anchor:
            try:
                list(coll.find({"location": {"$near": {"$geometry": anchor["location"], "$maxDistance": 2000}}}).limit(1))
                near_before = "ran without an index (unexpected)"
            except OperationFailure as exc:
                code_name = (exc.details or {}).get("codeName", "OperationFailure")
                first_line = " ".join(str((exc.details or {}).get("errmsg", exc)).split())[:120]
                near_before = f"refused by the server ({code_name}, code {exc.code}): \"{first_line}…\""

        stats_before = storage_stats(coll)
        create_indexes(coll)
        after = {name: measure(db, name, cmd, "after") for name, cmd in commands.items()}
        stats_after = storage_stats(coll)
        usage = index_usage(coll)

    summary = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server_version": server_version,
        "host": f"{platform.system()} {platform.machine()} (local mongod, 127.0.0.1)",
        "runs_per_query": RUNS,
        "documents": doc_count,
        "geo_anchor_animal_id": anchor.get("animal_id") if anchor else None,
        "near_query_before_index": near_before,
        "before": before,
        "after": after,
        "storage_before": stats_before,
        "storage_after": stats_after,
        "index_ops_since_restart": usage,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (OUT / "SUMMARY.md").write_text(render_markdown(summary))
    print(render_markdown(summary))
    return 0


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


def render_markdown(s: Dict[str, Any]) -> str:
    lines = [
        "# explain() before/after — summary",
        "",
        f"Generated by `scripts/explain_report.py` on {s['generated_utc']}.",
        f"MongoDB {s['server_version']}, {s['host']}. Collection `aac.animals`: **{s['documents']:,} documents**.",
        f"Each query was explained {s['runs_per_query']} times with `executionStats`; time is the median.",
        "Raw output: `before_<query>.json` and `after_<query>.json` in this folder.",
        "",
        "| Query | Stage before → after | Docs examined before → after | Keys examined after | nReturned | Time ms (median of 5) before → after |",
        "|---|---|---|---|---|---|",
    ]
    for name, b in s["before"].items():
        a = s["after"][name]
        lines.append(
            f"| {name} | {b['stage']} → {a['stage']} | {b['totalDocsExamined']:,} → {a['totalDocsExamined']:,} "
            f"| {a['totalKeysExamined']:,} | {a['nReturned']:,} "
            f"| {b['executionTimeMillis_median']:g} → {a['executionTimeMillis_median']:g} |"
        )
    lines += [
        "",
        "Docs examined ÷ docs returned:",
        "",
    ]
    for name, b in s["before"].items():
        a = s["after"][name]
        n = max(a["nReturned"], 1)
        lines.append(
            f"- {name}: {b['totalDocsExamined'] / n:,.1f} before, {a['totalDocsExamined'] / n:,.1f} after"
        )
    lines += ["", "Full winning plans:", ""]
    for name, b in s["before"].items():
        a = s["after"][name]
        lines.append(f"- {name} before: `{b['plan']}`")
        lines.append(f"- {name} after: `{a['plan']}` (index: {', '.join(a['indexes_used']) or 'none'})")
    if s.get("near_query_before_index"):
        lines += ["", f"`$near` query with no 2dsphere index: {s['near_query_before_index']}."]
    sb, sa = s["storage_before"], s["storage_after"]
    lines += [
        "",
        "## Storage cost of the indexes",
        "",
        "| | Before | After |",
        "|---|---|---|",
        f"| Data size (uncompressed BSON) | {_kb(sb['size_bytes'])} | {_kb(sa['size_bytes'])} |",
        f"| Total index size | {_kb(sb['totalIndexSize_bytes'])} | {_kb(sa['totalIndexSize_bytes'])} |",
    ]
    for idx, size in sa["indexSizes_bytes"].items():
        lines.append(f"| `{idx}` | {_kb(sb['indexSizes_bytes'].get(idx, 0)) if idx in sb['indexSizes_bytes'] else '—'} | {_kb(size)} |")
    lines += [
        "",
        "Sizes come from `$collStats` storageStats (WiredTiger, compressed on disk for indexes).",
        "",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
