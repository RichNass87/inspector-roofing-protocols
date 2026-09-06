import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dir = "evidence/colten-26.2.9-local-creator-foundation-2026-09-03";
const framePath = `${dir}/creative-toolchain-inventory.json`;
const installPath = `${dir}/install-verification.json`;

const read = file => JSON.parse(readFileSync(join(root, file), "utf8"));

const EXPECTED_CLASSES = [
  { id: "unreal", form: "subScopes", count: 6 },
  { id: "developer-tooling", form: "importRequirements", count: 6 },
  { id: "ios", form: "subScopes", count: 6 },
  { id: "three-dimensional", form: "importRequirements", count: 7 },
  { id: "final-cut-pro", form: "importRequirements", count: 6 },
  { id: "logic-pro", form: "importRequirements", count: 5 }
];
const STATES = new Set(["not-collected", "collected-empty", "collected"]);
const PATH_ALLOWLIST = ["/Applications/", "/Library/", "/System/", "/usr/", "/opt/homebrew/", "/Users/Shared/"];

const failures = [];
const fail = (id, detail) => failures.push(`[${id}] ${detail}`);

for (const file of [framePath, installPath]) {
  if (!existsSync(join(root, file))) {
    console.error(`check-inventory: missing ${file}`);
    process.exit(2);
  }
}
const frame = read(framePath);
const install = read(installPath);
const classes = Array.isArray(frame.classes) ? frame.classes : [];

// 1-3: state binds itemCount and items
for (const c of classes) {
  const inv = c.assetInventory ?? {};
  const items = Array.isArray(inv.items) ? inv.items : null;
  if (!STATES.has(inv.state)) fail(`state:${c.id}`, `unknown state ${JSON.stringify(inv.state)}`);
  if (items === null) fail(`items:${c.id}`, "items is not an array");
  else if (inv.state === "collected") {
    if (inv.itemCount !== items.length) fail(`1:${c.id}`, `collected but itemCount ${inv.itemCount} != items.length ${items.length}`);
    if (items.length === 0) fail(`1:${c.id}`, "collected with zero items; use collected-empty");
  } else if (inv.state === "collected-empty") {
    if (inv.itemCount !== 0) fail(`2:${c.id}`, `collected-empty but itemCount is ${JSON.stringify(inv.itemCount)}, not 0`);
    if (items.length !== 0) fail(`2:${c.id}`, `collected-empty but items has ${items.length} entries`);
  } else if (inv.state === "not-collected") {
    if (inv.itemCount !== null) fail(`3:${c.id}`, `not-collected but itemCount is ${JSON.stringify(inv.itemCount)}, not null`);
    if (items.length !== 0) fail(`3:${c.id}`, `not-collected but items has ${items.length} entries`);
    if (inv.collectedOn !== null) fail(`3:${c.id}`, "not-collected but collectedOn is set");
  }
  if (inv.state !== "not-collected" && typeof inv.collectedOn !== "string") {
    fail(`date:${c.id}`, `${inv.state} requires a collectedOn date string`);
  }
}

// 4: summary counters sum to classesDefined, and match the classes array
const summary = frame.summary ?? {};
const counted = { collected: 0, "collected-empty": 0, "not-collected": 0 };
for (const c of classes) if (c.assetInventory?.state in counted) counted[c.assetInventory.state] += 1;
const declaredEmpty = summary.classesCollectedEmpty ?? 0;
if (summary.classesDefined !== classes.length) fail("4", `classesDefined ${summary.classesDefined} != classes.length ${classes.length}`);
if (summary.classesCollected !== counted.collected) fail("4", `classesCollected ${summary.classesCollected} != actual ${counted.collected}`);
if (declaredEmpty !== counted["collected-empty"]) fail("4", `classesCollectedEmpty ${summary.classesCollectedEmpty ?? "(absent)"} != actual ${counted["collected-empty"]}`);
if (summary.classesNotCollected !== counted["not-collected"]) fail("4", `classesNotCollected ${summary.classesNotCollected} != actual ${counted["not-collected"]}`);
if (summary.classesCollected + declaredEmpty + summary.classesNotCollected !== summary.classesDefined) {
  fail("4", "classesCollected + classesCollectedEmpty + classesNotCollected != classesDefined");
}
if (counted["collected-empty"] > 0 && !("classesCollectedEmpty" in summary)) {
  fail("4", "a class is collected-empty but summary.classesCollectedEmpty is absent");
}

// 5: assetLevelDataImported iff any class has left not-collected
const anyImported = classes.some(c => c.assetInventory?.state !== "not-collected");
if (frame.assetLevelDataImported !== anyImported) {
  fail("5", `assetLevelDataImported is ${frame.assetLevelDataImported} but ${anyImported ? "a class is populated" : "no class is populated"}`);
}
const enumerated = classes.filter(c => c.assetInventory?.state === "collected").reduce((n, c) => n + c.assetInventory.items.length, 0);
if (!anyImported && summary.totalAssetsEnumerated !== null) fail("5", "totalAssetsEnumerated must be null while every class is not-collected");
if (anyImported && summary.totalAssetsEnumerated !== enumerated) fail("5", `totalAssetsEnumerated ${summary.totalAssetsEnumerated} != actual ${enumerated}`);

// 6: every item names the command that produced it
for (const c of classes) {
  for (const [i, item] of (c.assetInventory?.items ?? []).entries()) {
    if (typeof item.collectionMethod !== "string" || !item.collectionMethod.trim()) {
      fail(`6:${c.id}`, `items[${i}] has no collectionMethod; a value with no command behind it is a guess`);
    }
    if (item.namePublished === false && "name" in item) fail(`6:${c.id}`, `items[${i}] withholds the name but still carries a name field`);
    if (item.namePublished !== true && item.namePublished !== false) fail(`6:${c.id}`, `items[${i}] must carry namePublished true or false`);
  }
}

// 7: frozen surface, class ids, order, and requirement counts
if (classes.length !== EXPECTED_CLASSES.length) fail("7", `expected ${EXPECTED_CLASSES.length} classes, found ${classes.length}`);
EXPECTED_CLASSES.forEach((exp, i) => {
  const c = classes[i];
  if (!c) return;
  if (c.id !== exp.id) fail("7", `classes[${i}] is ${c.id}, expected ${exp.id}`);
  const hasFlat = Array.isArray(c.importRequirements);
  const hasGrouped = Array.isArray(c.subScopes);
  if (hasFlat && hasGrouped) fail(`7:${c.id}`, "carries both importRequirements and subScopes");
  if (!hasFlat && !hasGrouped) fail(`7:${c.id}`, "carries neither importRequirements nor subScopes");
  const actual = exp.form === "subScopes" ? c.subScopes?.length : c.importRequirements?.length;
  if (actual !== exp.count) fail(`7:${c.id}`, `${exp.form} count ${actual} != frozen ${exp.count}`);
});

// 8: no published absolute path outside the allowlist, in either file
function walkStrings(value, path, visit) {
  if (typeof value === "string") visit(value, path);
  else if (Array.isArray(value)) value.forEach((v, i) => walkStrings(v, `${path}[${i}]`, visit));
  else if (value && typeof value === "object") for (const [k, v] of Object.entries(value)) walkStrings(v, path ? `${path}.${k}` : k, visit);
}
for (const [label, doc] of [["frame", frame], ["install", install]]) {
  walkStrings(doc, "", (s, p) => {
    const tokens = s.match(/(?:^|[\s"'`(])(\/[^\s"'`)]+)/g) ?? [];
    for (const raw of tokens) {
      const token = raw.replace(/^[\s"'`(]/, "");
      if (token === "/" || /^\/[A-Za-z0-9_-]+\/?$/.test(token) && !token.startsWith("/Users")) continue;
      if (!PATH_ALLOWLIST.some(prefix => token.startsWith(prefix))) fail("8", `${label} ${p}: absolute path outside allowlist: ${token}`);
    }
  });
}

// 9: the two Xcode-anchored classes match by prefix, never by equality
const detected = install.capabilityStatus?.detectedApplications ?? [];
const xcodeEntry = detected.find(a => typeof a.name === "string" && a.name.startsWith("Xcode"));
if (!xcodeEntry) fail("9", "no detectedApplications entry begins with Xcode");
for (const id of ["developer-tooling", "ios"]) {
  const c = classes.find(x => x.id === id);
  const name = c?.hostApplication?.name;
  if (typeof name !== "string" || !name.startsWith("Xcode")) fail(`9:${id}`, `hostApplication.name ${JSON.stringify(name)} does not begin with Xcode`);
}

if (failures.length) {
  console.error(`check-inventory: FAIL (${failures.length})`);
  for (const f of failures) console.error(`  ${f}`);
  process.exit(1);
}
console.log(`check-inventory: PASS (${classes.length} classes, ${enumerated} assets enumerated, assetLevelDataImported=${frame.assetLevelDataImported})`);
