import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Scans added lines of the staged diff, or every line of a file given as argv[2], for
// identifiers that must never be published. Fails closed: exits 1 on any hit, and exits 1
// when zero lines were scanned, so a scan that reads nothing can never pass.
//
// The pattern table deliberately carries no account identifier. Home paths are caught by
// their root, not by any username. The gate's own file is excluded from the diff at the
// git level because its pattern source would otherwise match itself.

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SELF = "scripts/privacy-gate.mjs";

// Roots that carry an account name or host identity. Kept narrower than the inventory
// checker's allowlist on purpose: the gate scans every staged file, and API routes such
// as /v1/protocols in openapi.yaml are legitimate absolute-looking paths.
const HOME_ROOTS = /(?<![\w/.])\/(?:users(?!\/shared\/)|home|volumes|private|var\/folders)\/[^\s"'`)\]]+/i;

const PATTERNS = [
  ["home or volume path", HOME_ROOTS],
  ["team identifier", /\bDEVELOPMENT_TEAM\s*=\s*"?[A-Z0-9]{10}\b/],
  ["signing identity", /\b(?:Apple (?:Development|Distribution)|iPhone (?:Developer|Distribution)|Developer ID (?:Application|Installer)|Mac Developer)\b[^\n]{0,80}\([A-Z0-9]{10}\)/],
  ["provisioning profile", /\.mobileprovision\b/],
  ["licence key", /\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3,}\b/],
  ["cert fingerprint", /(?:fingerprint|SHA-?1|Authority=)[^\n]{0,40}\b[0-9a-f]{40}\b/i]
];

function addedLinesOf(diff) {
  const out = [];
  let prev = "";
  for (const line of diff.split("\n")) {
    // A real file header is "+++ " immediately after "--- ". An added content line that
    // happens to begin with "++ " is content, and is scanned.
    if (line.startsWith("+++ ") && prev.startsWith("--- ")) { prev = line; continue; }
    if (line.startsWith("+")) out.push(line.slice(1));
    prev = line;
  }
  return out;
}

const arg = process.argv[2];
let lines;
let source;
if (arg) {
  lines = readFileSync(arg, "utf8").split("\n");
  source = `file ${arg}`;
} else {
  const diff = execFileSync("git", ["diff", "--cached", "--", ".", `:(exclude)${SELF}`], { cwd: root, encoding: "utf8" });
  lines = addedLinesOf(diff);
  source = "staged diff";
}

const scanned = lines.filter(l => l.trim().length > 0);
if (scanned.length === 0) {
  console.error(`privacy-gate: FAIL (nothing to scan in ${source})`);
  process.exit(1);
}

const hits = [];
for (const line of scanned) {
  for (const [label, re] of PATTERNS) {
    const m = line.match(re);
    if (m) hits.push([label, m[0]]);
  }
}

if (hits.length) {
  console.error(`privacy-gate: FAIL (${hits.length} hit(s) in ${source})`);
  for (const [label, value] of hits.slice(0, 40)) console.error(`  ${label}: ${value}`);
  process.exit(1);
}
console.log(`privacy-gate: PASS (${scanned.length} lines scanned from ${source})`);
