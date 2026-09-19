"""
data/*.json validation utility.

ARCHITECTURE CHANGE (post-Phase-9): the PostgreSQL "policies" table,
the Policy ORM model, and this module's former import_policies_from_json()
(which loaded data/*.json into that table) have all been retired - policy
data is now sourced directly from data/*.json at request time via
backend/policy_loader.py (which also assigns each policy a stable id -
see its module docstring), for every consumer (the legacy `GET /policies`
route, the `GET /api/policies*` blueprint, and backend/retrieval.py).
There is no PostgreSQL "import" step left to run, so `flask
import-policies` has been removed from backend/app.py.

validate_json_files() survives unchanged (it never touched the database
in the first place - see its own docstring) because the data-integrity
guarantees it checks (malformed JSON, missing required fields, true
(name, sector) duplicates) are exactly as relevant to a JSON-only pipeline
as they were to a JSON-into-Postgres one - see tests/test_policy_loader.py
and tests/test_policy_data_integrity.py, which call it directly.
"""

import json
import os

REQUIRED_FIELDS = ("name", "category", "sub_category", "change", "impact")


def _default_data_dir():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data")


def validate_json_files(data_dir=None):
    """Read-only inspection of data/*.json - never touches the database.

    Returns a report dict:
      files: {filename: record_count}
      total_records: int (the REAL current count - never hardcoded)
      sectors: {sector: count}
      malformed_files: [{"file", "error"}]
      missing_field_records: [{"file", "index", "field"}]
      shared_names_across_sectors: {name: [sector, ...]} - INFORMATIONAL
          only, not an error. See Policy model's docstring: 8 real policy
          names legitimately appear under two sectors with distinct
          content - this is expected, not a data quality problem.
      true_duplicates: [{"name", "sector"}] - the SAME (name, sector)
          pair appearing more than once within the source data. This IS
          a real problem (unlike the above) because it violates the
          table's actual uniqueness key.
      ok: bool - True only if there are no malformed files, no missing
          required fields, and no true duplicates.
    """
    data_dir = data_dir or _default_data_dir()

    report = {
        "files": {},
        "total_records": 0,
        "sectors": {},
        "malformed_files": [],
        "missing_field_records": [],
        "shared_names_across_sectors": {},
        "true_duplicates": [],
        "ok": True,
    }

    if not os.path.isdir(data_dir):
        report["ok"] = False
        report["malformed_files"].append({"file": data_dir, "error": "data directory not found"})
        return report

    name_sector_seen = set()
    name_to_sectors = {}

    for filename in sorted(os.listdir(data_dir)):
        if not filename.endswith(".json"):
            continue
        sector = filename[:-len(".json")]
        file_path = os.path.join(data_dir, filename)

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            report["malformed_files"].append({"file": filename, "error": str(exc)})
            report["ok"] = False
            continue

        if not isinstance(data, list):
            report["malformed_files"].append({
                "file": filename, "error": "top-level JSON value is not a list",
            })
            report["ok"] = False
            continue

        report["files"][filename] = len(data)
        report["total_records"] += len(data)
        report["sectors"][sector] = report["sectors"].get(sector, 0) + len(data)

        for index, record in enumerate(data):
            if not isinstance(record, dict):
                report["missing_field_records"].append({
                    "file": filename, "index": index, "field": "(record is not an object)",
                })
                report["ok"] = False
                continue

            for field in REQUIRED_FIELDS:
                if not record.get(field):
                    report["missing_field_records"].append({
                        "file": filename, "index": index, "field": field,
                    })
                    report["ok"] = False

            name = record.get("name")
            if name is not None:
                key = (name, sector)
                if key in name_sector_seen:
                    report["true_duplicates"].append({"name": name, "sector": sector})
                    report["ok"] = False
                else:
                    name_sector_seen.add(key)
                name_to_sectors.setdefault(name, []).append(sector)

    report["shared_names_across_sectors"] = {
        name: sectors for name, sectors in name_to_sectors.items() if len(sectors) > 1
    }

    return report
