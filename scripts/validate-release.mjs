import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const VERSION = "1.1.1";
const RELEASE_DATE = "2026-07-16";
const AUTHOR = "Richard Amir Nasser";
const ORGANIZATION = "Inspector Roofing and Restoration";
const DOC_DOI = "10.5281/zenodo.20360964";
const SOURCE_DOI = "10.5281/zenodo.20435828";

const read = file => readFileSync(join(root, file), "utf8");
const json = file => JSON.parse(read(file));
const hasAll = (text, values) => values.every(value => text.includes(value));

const coreFiles = [
  "README.md",
  "METHODOLOGY.md",
  "VALIDATOR.md",
  "CHANGELOG.md",
  "CITATION.cff",
  "LICENSE.md",
  "LICENSE-CODE.md",
  "package.json",
  "pyproject.toml",
  "release-manifest.json",
  "roof-damage-verification.v1.json",
  "roof_inspection.schema.json",
  "rooffile-protocol.schema.json",
  "negative-evidence-record.schema.json",
  "damage_labels.json",
  "inspector-roofing-entity-schema.jsonld",
  "openapi.yaml",
  "server.js",
  "evidence/credential-evidence-register.md",
  "evidence/credential-evidence-register.json",
  "examples/anonymized-roof-documentation-packet/README.md",
  "examples/anonymized-roof-documentation-packet/inspection.json",
  "examples/anonymized-roof-documentation-packet/photos/manifest.csv",
  "examples/anonymized-roof-documentation-packet/photos/README.md",
  "examples/anonymized-roof-documentation-packet/reports/roof-report.md"
];

const missingCoreFiles = coreFiles.filter(file => !existsSync(join(root, file)));
const manifest = json("release-manifest.json");
const packageJson = json("package.json");
const protocol = json("roof-damage-verification.v1.json");
const credentials = json("evidence/credential-evidence-register.json");
const sample = json("examples/anonymized-roof-documentation-packet/inspection.json");
const entitySchema = json("inspector-roofing-entity-schema.jsonld");

const coreText = coreFiles.filter(file => existsSync(join(root, file))).map(read).join("\n");
const wikidataScanFiles = [
  "README.md",
  "index.html",
  "docs/index.html",
  "selected-book-items.md",
  "richard-amir-nasser-ai-research-books.md",
  "related-research/roofing-near-me-ai-visibility-study.md",
  "huggingface/richard-amir-nasser-ai-research-books/README.md",
  "huggingface/richard-amir-nasser-ai-research-books/books.json"
];
const allowedWikidataIds = new Set(["Q140522693"]);
const referencedWikidataIds = [...new Set(
  wikidataScanFiles
    .filter(file => existsSync(join(root, file)))
    .flatMap(file => [...read(file).matchAll(/https:\/\/www\.wikidata\.org\/wiki\/(Q\d+)/g)].map(match => match[1]))
)];
const unverifiedWikidataIds = referencedWikidataIds.filter(id => !allowedWikidataIds.has(id));
const report = {
  validator: "Inspector Roofing Protocols internal release-readiness validator",
  validatorVersion: "1.0.0",
  evaluatedAt: new Date().toISOString(),
  packageVersion: VERSION,
  minimumReleaseScore: 85,
  strongTargetScore: 91,
  independentCertification: false,
  categories: [],
  hardGates: [],
  findings: []
};

function category(name, points, checks) {
  const passed = checks.filter(check => check.ok).length;
  const earned = Number(((passed / checks.length) * points).toFixed(2));
  report.categories.push({ name, pointsAvailable: points, pointsEarned: earned, checks });
  return earned;
}

const identityChecks = [
  {
    name: "Release manifest uses the canonical title, version, author, organization, and planned date",
    ok: manifest.title === "Inspector Roofing Protocols" && manifest.version === VERSION &&
      manifest.author?.name === AUTHOR && manifest.publisher?.name === ORGANIZATION &&
      manifest.releaseDate === RELEASE_DATE
  },
  {
    name: "Node package metadata uses the canonical version and author",
    ok: packageJson.name === "inspector-roofing-protocols" && packageJson.version === VERSION &&
      packageJson.author === AUTHOR
  },
  {
    name: "Citation metadata uses the canonical version, date, and author",
    ok: hasAll(read("CITATION.cff"), [
      `version: "${VERSION}"`, `date-released: "${RELEASE_DATE}"`,
      "given-names: \"Richard Amir\"", "family-names: \"Nasser\""
    ])
  },
  {
    name: "Active protocol metadata uses the canonical identity",
    ok: protocol.version === VERSION && protocol.maintainer?.name === AUTHOR &&
      protocol.maintainer?.organization === ORGANIZATION &&
      !/"name"\s*:\s*"Richard Nasser"/.test(read("inspector-roofing-entity-schema.jsonld"))
  }
];

const sourceGraphChecks = [
  {
    name: "Documentation DOI relationship is explicit",
    ok: hasAll(coreText, [DOC_DOI, "public documentation record"])
  },
  {
    name: "Software/source package DOI relationship is explicit",
    ok: hasAll(coreText, [SOURCE_DOI, "software/source package record"])
  },
  {
    name: "ORCID, repository, canonical hub, and public Protocols page are linked",
    ok: hasAll(coreText, [
      "https://orcid.org/0009-0000-2980-7543",
      "https://github.com/RichNass87/inspector-roofing-protocols",
      "https://inspector-roofing.com/richard-nasser/",
      "https://inspector-roofing.com/inspector-roofing-protocols/"
    ])
  }
];

const methodologyChecks = [
  {
    name: "Methodology defines collection, review, privacy, credential, insurance, and reproducibility rules",
    ok: hasAll(read("METHODOLOGY.md"), [
      "## Collection sequence", "## Review rules", "## Privacy and redaction",
      "## Credential handling", "## Insurance and public-adjuster boundary", "## Reproducibility"
    ])
  },
  {
    name: "Core JSON schemas and taxonomy parse and use the release version where applicable",
    ok: [
      "roof_inspection.schema.json",
      "rooffile-protocol.schema.json",
      "negative-evidence-record.schema.json",
      "damage_labels.json"
    ].every(file => {
      try {
        JSON.parse(read(file));
        return true;
      } catch {
        return false;
      }
    }) && json("damage_labels.json").version === VERSION
  },
  {
    name: "Synthetic sample is deidentified and all observation/photo references resolve",
    ok: (() => {
      const observationIds = new Set(sample.observations.map(item => item.observation_id));
      const photoIds = new Set(sample.photo_manifest.map(item => item.photo_id));
      const observationPhotosResolve = sample.observations.every(item =>
        item.evidence_photo_ids.every(id => photoIds.has(id))
      );
      const recommendationObservationsResolve = sample.recommendations.every(item =>
        item.related_observation_ids.every(id => observationIds.has(id))
      );
      return sample.property.address === "DEIDENTIFIED" &&
        sample.property.postal_code === "00000" &&
        sample.report_version === VERSION &&
        observationPhotosResolve && recommendationObservationsResolve;
    })()
  }
];

function checksumState() {
  if (!existsSync(join(root, "CHECKSUMS.sha256"))) return { ok: false, detail: "CHECKSUMS.sha256 is missing" };
  const lines = read("CHECKSUMS.sha256").trim().split("\n").filter(Boolean);
  const entries = new Map();
  for (const line of lines) {
    const match = line.match(/^([a-f0-9]{64})  (.+)$/);
    if (!match) return { ok: false, detail: `Malformed checksum line: ${line}` };
    entries.set(match[2], match[1]);
  }
  const required = [...coreFiles, "scripts/generate-checksums.mjs", "scripts/validate-release.mjs"];
  for (const file of required) {
    if (!entries.has(file)) return { ok: false, detail: `Missing checksum entry: ${file}` };
    const actual = createHash("sha256").update(readFileSync(join(root, file))).digest("hex");
    if (actual !== entries.get(file)) return { ok: false, detail: `Stale checksum entry: ${file}` };
  }
  return { ok: true, detail: `${entries.size} checksum entries verified for required release files` };
}

const checksums = checksumState();
const reproducibilityChecks = [
  {
    name: "Runtime references shipped root files rather than missing directories",
    ok: hasAll(read("server.js"), [
      "roof-damage-verification.v1.json", "openapi.yaml", "sample-request.json"
    ]) && !read("server.js").includes('join(__dirname, "api/') &&
      !read("server.js").includes('join(__dirname, "protocols/')
  },
  {
    name: "Release manifest and dual licenses are complete",
    ok: missingCoreFiles.length === 0 && packageJson.license === "MIT" && packageJson.private === true &&
      read("LICENSE.md").includes("CC BY 4.0") && read("LICENSE-CODE.md").includes("MIT License")
  },
  {
    name: "SHA-256 ledger covers the required release files and is current",
    ok: checksums.ok,
    detail: checksums.detail
  }
];

const unresolvedPlaceholders = /REPLACE_WITH|hello@example\.com|inspector-roofing\.example/.test(coreText);
const restrictedFieldAssignment = /(?:date[_ -]?of[_ -]?birth|d\.o\.b\.|policy[_ -]?number|claim[_ -]?number|certificate[_ -]?number)\s*[":=]+\s*(?!null|excluded|omitted|private|not included)[A-Z0-9]/i.test(coreText);
const forbiddenBinary = coreFiles.some(file => /\.(?:heic|jpe?g|png)$/i.test(file));
const boundaryText = "does not act as a public adjuster";
const carrierText = "carriers decide coverage, payment, and claim outcomes";
const boundaryFiles = [
  "README.md",
  "METHODOLOGY.md",
  "roof-damage-verification.v1.json",
  "evidence/credential-evidence-register.md",
  "examples/anonymized-roof-documentation-packet/inspection.json"
];
const overclaimPattern = /guaranteed insurance|guaranteed compliance|universal no\.?\s*1|registered trademark|platform[- ]endorsed|claim-verifiable|carrier-readable|adjuster-readable|code-compliant workmanship/i;
const privacyBoundaryChecks = [
  {
    name: "Core release files contain no unresolved placeholder metadata",
    ok: !unresolvedPlaceholders
  },
  {
    name: "Public sample and release scope contain no restricted private-field assignments or credential image",
    ok: !restrictedFieldAssignment && !forbiddenBinary
  },
  {
    name: "Insurance and public-adjuster boundary appears across the evidence spine",
    ok: boundaryFiles.every(file => {
      const text = read(file).toLowerCase();
      return text.includes(boundaryText) && text.includes(carrierText);
    })
  },
  {
    name: "Core release avoids ranking, platform, trademark, insurance, and compliance overclaims",
    ok: !overclaimPattern.test(coreText)
  },
  {
    name: "Public release files reference only the verified live Wikidata allowlist",
    ok: unverifiedWikidataIds.length === 0 && referencedWikidataIds.includes("Q140522693"),
    detail: unverifiedWikidataIds.length
      ? `Unverified or missing Wikidata identifiers: ${unverifiedWikidataIds.join(", ")}`
      : "Only verified live item Q140522693 is referenced"
  }
];

const faa = credentials.credentials.find(item => item.type === "FAA Remote Pilot");
const garca = credentials.credentials.find(item => item.type.includes("GARCA"));
const haag = credentials.credentials.find(item => item.type.includes("HAAG"));
const credentialChecks = [
  {
    name: "FAA official registry result records source name, certificate type, rating, and issue date",
    ok: faa?.sourceUrl === "https://amsrvs.registry.faa.gov/airmeninquiry/Main.aspx" &&
      faa?.sourceDisplayName === "Richard James Nasser" &&
      faa?.status.includes("Remote Pilot") && faa?.status.includes("Small Unmanned Aircraft System") &&
      faa?.issueDate === "2026-04-01"
  },
  {
    name: "FAA name bridge and private-card exclusion are explicit",
    ok: faa?.privateFieldsExcluded === true &&
      faa?.identityBridge.includes("First-party") &&
      hasAll(read("evidence/credential-evidence-register.md"), [
        "middle-name difference", "Do not post the raw card"
      ])
  },
  {
    name: "GARCA record is limited to a voluntary license/member profile",
    ok: garca?.sourceUrl === "https://garca.org/Sys/PublicProfile/69801316/6512329" &&
      garca?.identifier === "C8467440" && garca?.limitations.includes("Not a Georgia state contractor license")
  },
  {
    name: "HAAG capture is dated and labeled as first-party rather than issuer verification",
    ok: haag?.sourceClass.includes("first-party-hosted") &&
      haag?.displayedExpiration === "2026-10-31" &&
      haag?.limitations.includes("Not an issuer permalink")
  }
];

const publicReleaseChecks = [
  {
    name: "v1.1.1 public release is recorded only after publication",
    ok: manifest.releaseState === "published" &&
      manifest.publicReleaseUrl === "https://github.com/RichNass87/inspector-roofing-protocols/releases/tag/v1.1.1"
  }
];

let score = 0;
score += category("Identity and version consistency", 20, identityChecks);
score += category("Persistent source graph", 15, sourceGraphChecks);
score += category("Methodology and sample", 15, methodologyChecks);
score += category("Reproducibility and integrity", 15, reproducibilityChecks);
score += category("Privacy and claim boundaries", 20, privacyBoundaryChecks);
score += category("Credential evidence quality", 10, credentialChecks);
score += category("Published release", 5, publicReleaseChecks);
report.score = Number(score.toFixed(2));

function hardGate(name, ok, detail) {
  report.hardGates.push({ name, ok, detail });
}

hardGate("Core files present", missingCoreFiles.length === 0, missingCoreFiles.join(", ") || "All core files present");
hardGate("Canonical identity and version consistent", identityChecks.every(check => check.ok), "See identity checks");
hardGate("No unresolved placeholders", !unresolvedPlaceholders, "Core release files scanned");
hardGate("No restricted private fields", !restrictedFieldAssignment && !forbiddenBinary, "Public release scope scanned");
hardGate("No missing or deleted Wikidata identifiers", unverifiedWikidataIds.length === 0, unverifiedWikidataIds.join(", ") || "Only allowlisted live identifiers are present");
hardGate("Insurance/public-adjuster boundary complete", privacyBoundaryChecks[2].ok, "Five evidence-spine files checked");
hardGate("Checksum ledger current", checksums.ok, checksums.detail);
hardGate("Minimum score reached", report.score >= report.minimumReleaseScore, `${report.score}/${report.minimumReleaseScore}`);

const failedHardGates = report.hardGates.filter(gate => !gate.ok);
if (failedHardGates.length) report.status = "RELEASE_HOLD";
else if (manifest.releaseState === "published") report.status = "PASS";
else report.status = "READY_FOR_SCHEDULED_RELEASE";

report.findings.push("Score is an internal package-readiness result, not an independent certification or search ranking.");
if (manifest.releaseState !== "published") {
  report.findings.push("The staged package can score at most 95. Publish and verify the v1.1.1 release before changing releaseState to published.");
}
report.findings.push("FAA evidence uses a disclosed first-party bridge from canonical Richard Amir Nasser to the registry name Richard James Nasser; private card fields remain excluded.");
report.findings.push("The official Wikidata API check on 2026-07-16 verified Q140522693 as live; missing or deleted Q identifiers are excluded.");

writeFileSync(join(root, "release-readiness-report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");

const markdown = [
  "# Inspector Roofing Protocols release-readiness report",
  "",
  `- Evaluated: ${report.evaluatedAt}`,
  `- Version: ${report.packageVersion}`,
  `- Score: **${report.score}/100**`,
  `- Release minimum: **${report.minimumReleaseScore}/100**`,
  `- Strong target: **${report.strongTargetScore}/100**`,
  `- Status: **${report.status}**`,
  `- Independent certification: **No**`,
  "",
  "## Category results",
  "",
  "| Category | Earned | Available |",
  "| --- | ---: | ---: |",
  ...report.categories.map(item => `| ${item.name} | ${item.pointsEarned} | ${item.pointsAvailable} |`),
  "",
  "## Hard gates",
  "",
  ...report.hardGates.map(item => `- ${item.ok ? "PASS" : "FAIL"}: ${item.name} - ${item.detail}`),
  "",
  "## Boundaries",
  "",
  ...report.findings.map(item => `- ${item}`),
  ""
].join("\n");

writeFileSync(join(root, "release-readiness-report.md"), markdown, "utf8");
console.log(`${report.status}: ${report.score}/100`);
if (failedHardGates.length) process.exitCode = 1;
