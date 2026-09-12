"""
Phase 7: data/*.json -> policies table import.

Two separate concerns, deliberately:
  - validate_json_files(): read-only, no database access at all. Detects
    malformed JSON, missing required fields, and structural issues
    before any import is attempted.
  - import_policies_from_json(): the actual idempotent, transaction-
    wrapped import, built on top of validate_json_files() - aborts with
    zero database writes if validation finds a hard problem.

Run via the Flask CLI (registered in app.py):
    flask import-policies
    flask import-policies --dry-run

Or call import_policies_from_json() directly from Python (tests do this).
"""

import json
import os

from database import db
from models import Policy

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


def import_policies_from_json(data_dir=None, dry_run=False):
    """Idempotent, transaction-wrapped import of data/*.json into the
    policies table.

    Matches existing rows on (name, sector) - see Policy model's
    docstring for why that pair, not name alone, is the natural key.
    Safe to run more than once: re-running with unchanged JSON reports
    everything as "skipped" and writes nothing.

    Returns:
      {"inserted": int, "updated": int, "skipped": int,
       "validation": <validate_json_files() report>,
       "aborted": bool, "abort_reason": str | None}

    Aborts with ZERO database writes if validation finds malformed JSON,
    missing required fields, or true (name, sector) duplicates in the
    source - a partial import of bad data is worse than no import at
    all.

    dry_run=True runs the full comparison logic (so the report is
    accurate) but rolls back instead of committing - useful for a
    preview before actually writing.
    """
    validation = validate_json_files(data_dir)
    report = {
        "inserted": 0, "updated": 0, "skipped": 0,
        "validation": validation,
        "aborted": False, "abort_reason": None,
    }

    if not validation["ok"]:
        report["aborted"] = True
        report["abort_reason"] = (
            "Source JSON failed validation (malformed files, missing "
            "required fields, or true duplicate (name, sector) pairs) - "
            "see the 'validation' report for details. No database "
            "changes were made."
        )
        return report

    data_dir = data_dir or _default_data_dir()

    try:
        for filename in sorted(os.listdir(data_dir)):
            if not filename.endswith(".json"):
                continue
            sector = filename[:-len(".json")]
            with open(os.path.join(data_dir, filename), encoding="utf-8") as f:
                records = json.load(f)

            for record in records:
                name = record["name"]
                existing = Policy.query.filter_by(name=name, sector=sector).first()

                if existing is None:
                    if not dry_run:
                        new_policy = Policy(
                            name=name,
                            sector=sector,
                            category=record["category"],
                            sub_category=record["sub_category"],
                            change=record["change"],
                            impact=record["impact"],
                            source_file=filename,
                        )
                        db.session.add(new_policy)
                    report["inserted"] += 1
                    continue

                changed = (
                    existing.category != record["category"]
                    or existing.sub_category != record["sub_category"]
                    or existing.change != record["change"]
                    or existing.impact != record["impact"]
                    or existing.source_file != filename
                )
                if changed:
                    if not dry_run:
                        existing.category = record["category"]
                        existing.sub_category = record["sub_category"]
                        existing.change = record["change"]
                        existing.impact = record["impact"]
                        existing.source_file = filename
                        db.session.add(existing)
                    report["updated"] += 1
                else:
                    report["skipped"] += 1

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

    except Exception:
        # Transaction-aware: any failure mid-import rolls back
        # everything from this run - no partial import is ever left
        # committed, whether the failure is on record 1 or record 150.
        db.session.rollback()
        raise

    return report
