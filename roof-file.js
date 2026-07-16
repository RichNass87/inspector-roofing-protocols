#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "..");

const REQUIRED_TOP_LEVEL = [
  "inspection_id",
  "inspection_date",
  "property",
  "inspector",
  "roof_system",
  "observations",
  "photo_manifest",
  "recommendations",
];

function readText(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), "utf8");
}

function copyText(dest, ...parts) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, readText(...parts), "utf8");
}

function defaultInspection() {
  return {
    inspection_id: "IR-YYYYMMDD-001",
    inspection_date: "YYYY-MM-DD",
    report_version: "1.1.0",
    property: {
      address: "",
      city: "",
      state: "",
      postal_code: "",
      year_built: null,
      stories: null,
      occupancy: null,
    },
    inspector: {
      name: "",
      organization: "Inspector Roofing and Restoration",
      license_number: null,
      contact_email: null,
      contact_phone: null,
    },
    weather_context: {
      inspection_weather: null,
      recent_storm_date: null,
      wind_event_claimed: null,
      hail_event_claimed: null,
      source_notes: null,
    },
    roof_system: {
      covering: "asphalt_shingle",
      manufacturer: null,
      approximate_age_years: null,
      slope: null,
      drainage: null,
      access_method: "walked_roof",
      limitations: [],
    },
    observations: [],
    photo_manifest: [],
    recommendations: [],
    disclaimers: [
      "This file records observable conditions only. It does not determine insurance coverage, payment, causation, code compliance, engineering conclusions, or legal rights. Carriers decide coverage, payment, and claim outcomes."
    ],
  };
}

function init(target, force = false) {
  const dest = path.resolve(target);
  if (fs.existsSync(dest) && fs.readdirSync(dest).length > 0 && !force) {
    console.error(`error: ${dest} exists and is not empty. Use --force to write into it.`);
    process.exitCode = 2;
    return;
  }
  for (const folder of ["photos", "measurements", "reports", "checklists", "schemas", "taxonomies", "exports"]) {
    fs.mkdirSync(path.join(dest, folder), { recursive: true });
  }
  fs.writeFileSync(path.join(dest, "inspection.json"), JSON.stringify(defaultInspection(), null, 2) + "\n", "utf8");
  copyText(path.join(dest, "README.md"), "examples", "blank_roof_file", "README.md");
  copyText(path.join(dest, "photos", "manifest.csv"), "templates", "photo_manifest.csv");
  copyText(path.join(dest, "reports", "roof_report.md"), "templates", "roof_report.md");
  copyText(path.join(dest, "checklists", "report_checklist.md"), "checklists", "report_checklist.md");
  copyText(path.join(dest, "schemas", "roof_inspection.schema.json"), "schemas", "roof_inspection.schema.json");
  copyText(path.join(dest, "taxonomies", "damage_labels.json"), "taxonomies", "damage_labels.json");
  console.log(`created roof inspection folder: ${dest}`);
}

function isFilled(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0 && !value.startsWith("YYYY");
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.values(value).some(isFilled);
  return true;
}

function score(file) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(path.resolve(file), "utf8"));
  } catch (err) {
    console.error(`error: ${err.message}`);
    process.exitCode = 2;
    return;
  }
  const checks = [];
  for (const key of REQUIRED_TOP_LEVEL) checks.push([key, key in data && isFilled(data[key])]);
  for (const key of ["address", "city", "state", "postal_code"]) checks.push([`property.${key}`, isFilled(data.property?.[key])]);
  for (const key of ["name", "organization"]) checks.push([`inspector.${key}`, isFilled(data.inspector?.[key])]);
  for (const key of ["covering", "approximate_age_years", "slope", "drainage", "access_method"]) checks.push([`roof_system.${key}`, isFilled(data.roof_system?.[key])]);
  const passed = checks.filter(([, ok]) => ok).length;
  const pct = Math.round((passed / checks.length) * 100);
  console.log(`roof file completeness score: ${pct}% (${passed}/${checks.length} checks)`);
  const missing = checks.filter(([, ok]) => !ok).map(([name]) => name);
  if (missing.length) {
    console.log("missing or blank fields:");
    for (const name of missing) console.log(`- ${name}`);
    process.exitCode = 1;
  } else {
    console.log("all required checklist fields are filled");
  }
}

function help() {
  console.log(`roof-file commands:\n  init <path> [--force]      create a blank roof inspection folder\n  schema                    print the roof inspection JSON schema\n  taxonomy                  print the roof damage label taxonomy\n  score <inspection.json>   score an inspection JSON file for basic completeness`);
}

const [command, arg, maybeForce] = process.argv.slice(2);
if (!command || command === "help" || command === "--help" || command === "-h") {
  help();
} else if (command === "init") {
  if (!arg) {
    console.error("error: init requires a path");
    process.exitCode = 2;
  } else {
    init(arg, maybeForce === "--force" || process.argv.includes("--force"));
  }
} else if (command === "schema") {
  console.log(readText("schemas", "roof_inspection.schema.json"));
} else if (command === "taxonomy") {
  console.log(readText("taxonomies", "damage_labels.json"));
} else if (command === "score") {
  if (!arg) {
    console.error("error: score requires a path to inspection.json");
    process.exitCode = 2;
  } else {
    score(arg);
  }
} else {
  console.error(`error: unknown command: ${command}`);
  help();
  process.exitCode = 2;
}
