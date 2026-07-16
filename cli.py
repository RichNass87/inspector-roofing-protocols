from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

PACKAGE = "inspector_roofing_protocols"
DATA_ROOT = resources.files(PACKAGE) / "data"
REQUIRED_TOP_LEVEL = [
    "inspection_id",
    "inspection_date",
    "property",
    "inspector",
    "roof_system",
    "observations",
    "photo_manifest",
    "recommendations",
]


def read_text_resource(*parts: str) -> str:
    return (DATA_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def copy_text_resource(target: Path, *parts: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(read_text_resource(*parts), encoding="utf-8")


def default_inspection() -> dict[str, Any]:
    return {
        "inspection_id": "IR-YYYYMMDD-001",
        "inspection_date": "YYYY-MM-DD",
        "report_version": "1.1.1",
        "property": {
            "address": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "year_built": None,
            "stories": None,
            "occupancy": None,
        },
        "inspector": {
            "name": "",
            "organization": "Inspector Roofing and Restoration",
            "license_number": None,
            "contact_email": None,
            "contact_phone": None,
        },
        "weather_context": {
            "inspection_weather": None,
            "recent_storm_date": None,
            "wind_event_claimed": None,
            "hail_event_claimed": None,
            "source_notes": None,
        },
        "roof_system": {
            "covering": "asphalt_shingle",
            "manufacturer": None,
            "approximate_age_years": None,
            "slope": None,
            "drainage": None,
            "access_method": "walked_roof",
            "limitations": [],
        },
        "observations": [],
        "photo_manifest": [],
        "recommendations": [],
        "disclaimers": [
            "This file records observable conditions only. It does not determine insurance coverage, payment, causation, code compliance, engineering conclusions, or legal rights. Carriers decide coverage, payment, and claim outcomes."
        ],
    }


def write_default_inspection(path: Path) -> None:
    path.write_text(json.dumps(default_inspection(), indent=2) + "\n", encoding="utf-8")


def init_roof_file(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser().resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"error: {target} exists and is not empty. Use --force to write into it.", file=sys.stderr)
        return 2

    for folder in ["photos", "measurements", "reports", "checklists", "schemas", "taxonomies", "exports"]:
        (target / folder).mkdir(parents=True, exist_ok=True)

    write_default_inspection(target / "inspection.json")
    copy_text_resource(target / "README.md", "samples", "roof_file_readme.md")
    copy_text_resource(target / "photos" / "manifest.csv", "templates", "photo_manifest.csv")
    copy_text_resource(target / "reports" / "roof_report.md", "templates", "roof_report.md")
    copy_text_resource(target / "checklists" / "report_checklist.md", "checklists", "report_checklist.md")
    copy_text_resource(target / "schemas" / "roof_inspection.schema.json", "schemas", "roof_inspection.schema.json")
    copy_text_resource(target / "taxonomies" / "damage_labels.json", "taxonomies", "damage_labels.json")

    print(f"created roof inspection folder: {target}")
    return 0


def print_schema(_args: argparse.Namespace) -> int:
    print(read_text_resource("schemas", "roof_inspection.schema.json"))
    return 0


def print_taxonomy(_args: argparse.Namespace) -> int:
    print(read_text_resource("taxonomies", "damage_labels.json"))
    return 0


def is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and not value.startswith("YYYY")
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return any(is_filled(v) for v in value.values())
    return True


def score_inspection(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2

    checks: list[tuple[str, bool]] = []
    for key in REQUIRED_TOP_LEVEL:
        checks.append((key, key in data and is_filled(data[key])))

    property_required = ["address", "city", "state", "postal_code"]
    for key in property_required:
        checks.append((f"property.{key}", is_filled(data.get("property", {}).get(key))))

    inspector_required = ["name", "organization"]
    for key in inspector_required:
        checks.append((f"inspector.{key}", is_filled(data.get("inspector", {}).get(key))))

    roof_required = ["covering", "approximate_age_years", "slope", "drainage", "access_method"]
    for key in roof_required:
        checks.append((f"roof_system.{key}", is_filled(data.get("roof_system", {}).get(key))))

    passed = sum(1 for _name, ok in checks if ok)
    score = round((passed / len(checks)) * 100) if checks else 0
    print(f"roof file completeness score: {score}% ({passed}/{len(checks)} checks)")

    missing = [name for name, ok in checks if not ok]
    if missing:
        print("missing or blank fields:")
        for name in missing:
            print(f"- {name}")
    else:
        print("all required checklist fields are filled")

    return 0 if score == 100 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="roof-file", description="Create and inspect structured roof inspection files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a blank roof inspection folder")
    p_init.add_argument("path", help="folder to create")
    p_init.add_argument("--force", action="store_true", help="write into an existing folder")
    p_init.set_defaults(func=init_roof_file)

    p_schema = sub.add_parser("schema", help="print the roof inspection JSON schema")
    p_schema.set_defaults(func=print_schema)

    p_taxonomy = sub.add_parser("taxonomy", help="print the roof damage label taxonomy")
    p_taxonomy.set_defaults(func=print_taxonomy)

    p_score = sub.add_parser("score", help="score an inspection.json file for basic completeness")
    p_score.add_argument("path", help="path to inspection.json")
    p_score.set_defaults(func=score_inspection)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
