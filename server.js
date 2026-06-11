import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8787);
const HOST = process.env.HOST || "127.0.0.1";

const PROTOCOL = JSON.parse(
  readFileSync(join(__dirname, "protocols/roof-damage-verification.v1.json"), "utf8")
);

const TRUST_SIGNALS = [
  ["hasStructuredData", 20, "Publish valid website JSON-LD."],
  ["hasPublicRepository", 15, "Publish the protocol and schemas in a public GitHub repo."],
  ["hasCitableRecords", 15, "Archive releases and datasets with DOI records."],
  ["hasAuthorIdentity", 10, "Connect Richard Nasser through ORCID and public author profiles."],
  ["hasDatasetDocumentation", 15, "Publish a dataset card, examples, and privacy notes."],
  ["hasApiContract", 15, "Publish an OpenAPI contract and callable endpoint."],
  ["hasPressOrThirdPartyReferences", 10, "Link third-party press, trust, or professional references."]
];

function send(res, status, body, contentType = "application/json") {
  const payload = contentType === "application/json" ? JSON.stringify(body, null, 2) : body;
  res.writeHead(status, {
    "content-type": `${contentType}; charset=utf-8`,
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type"
  });
  res.end(payload);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", chunk => {
      body += chunk;
      if (body.length > 1_000_000) {
        reject(new Error("Request body too large."));
        req.destroy();
      }
    });
    req.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch {
        reject(new Error("Request body must be valid JSON."));
      }
    });
    req.on("error", reject);
  });
}

function verifyRoofDamage(input) {
  const observations = Array.isArray(input.observations) ? input.observations : [];
  const errors = [];

  if (!input.inspectionContext?.inspectionDate) errors.push("inspectionContext.inspectionDate is required.");
  if (!input.inspectionContext?.locationRegion) errors.push("inspectionContext.locationRegion is required.");
  if (!input.roofSystem) errors.push("roofSystem is required.");
  if (!observations.length) errors.push("At least one observation is required.");

  if (errors.length) {
    return { ok: false, errors };
  }

  const stormCategories = new Set(["hail_candidate", "wind_candidate"]);
  const nonStormCategories = new Set([
    "mechanical_mark",
    "wear_and_age",
    "manufacturing_condition",
    "installation_condition",
    "maintenance_condition"
  ]);

  let stormCount = 0;
  let nonStormCount = 0;
  let gapCount = 0;
  let strongOrModerateCount = 0;

  const findings = observations.map((observation, index) => {
    const category = observation.category || "other";
    const quality = observation.evidenceQuality || "unknown";
    if (stormCategories.has(category)) stormCount += 1;
    if (nonStormCategories.has(category)) nonStormCount += 1;
    if (category === "documentation_gap" || quality === "insufficient" || quality === "weak") gapCount += 1;
    if (quality === "strong" || quality === "moderate") strongOrModerateCount += 1;

    return {
      label: category,
      rationale: observation.description || "Observation submitted without a detailed description.",
      relatedObservationIndexes: [index],
      evidenceQuality: quality
    };
  });

  let result = "mixed_or_inconclusive";
  if (gapCount === observations.length) result = "insufficient_evidence";
  else if (nonStormCount > stormCount) result = "non_storm_indicators_present";
  else if (gapCount > 0) result = "field_review_required";
  else if (stormCount > 0 && nonStormCount === 0 && strongOrModerateCount > 0) result = "consistent_with_storm_damage";

  const evidenceGaps = [];
  if (gapCount > 0) evidenceGaps.push("Some observations have weak, insufficient, or incomplete documentation.");
  if (!Array.isArray(input.media) || input.media.length === 0) evidenceGaps.push("No media references were supplied.");
  if (!input.inspectionContext?.eventDate) evidenceGaps.push("No storm or event date was supplied.");

  const recommendedActions = [
    "Preserve overview photos, slope-level photos, close-ups, and scale references.",
    "Confirm date, area, and roof-system context for each observation.",
    "Use field review when media quality or context is insufficient."
  ];

  if (result === "non_storm_indicators_present") {
    recommendedActions.unshift("Separate possible storm indicators from wear, installation, maintenance, or mechanical conditions.");
  }

  return {
    ok: true,
    response: {
      protocolVersion: PROTOCOL.version,
      result,
      confidence: Number(Math.max(0.2, Math.min(0.95, (strongOrModerateCount + 1) / (observations.length + 2))).toFixed(2)),
      findings,
      evidenceGaps,
      recommendedActions,
      citations: PROTOCOL.citations
    }
  };
}

function evaluateTrustIndex(input) {
  const signals = input.signals || {};
  let score = 0;
  const missingSignals = [];
  const recommendations = [];

  for (const [key, weight, recommendation] of TRUST_SIGNALS) {
    if (signals[key] === true) score += weight;
    else {
      missingSignals.push(key);
      recommendations.push(recommendation);
    }
  }

  let grade = "emerging";
  if (score >= 80) grade = "agent_ready";
  else if (score >= 60) grade = "citable";
  else if (score >= 35) grade = "documented";

  return {
    score,
    grade,
    missingSignals,
    recommendations
  };
}

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (req.method === "OPTIONS") return send(res, 204, {});
  if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true });

  if (req.method === "GET" && url.pathname === "/api/openapi.yaml") {
    const yaml = readFileSync(join(__dirname, "api/openapi.yaml"), "utf8");
    return send(res, 200, yaml, "text/yaml");
  }

  if (req.method === "GET" && url.pathname === "/v1/protocols") {
    return send(res, 200, {
      protocols: [
        {
          id: PROTOCOL.protocol_id,
          name: PROTOCOL.name,
          version: PROTOCOL.version,
          url: "https://REPLACE_WITH_GITHUB_REPO_URL/blob/main/protocols/roof-damage-verification.v1.json",
          schema: "https://REPLACE_WITH_DOMAIN/schemas/rooffile-protocol.schema.json",
          doi: "REPLACE_WITH_ZENODO_DOI_URL"
        }
      ]
    });
  }

  if (req.method === "POST" && url.pathname === "/v1/roof-damage/verify") {
    try {
      const input = await readJson(req);
      const result = verifyRoofDamage(input);
      if (!result.ok) return send(res, 400, { error: "Invalid roof damage verification request.", details: result.errors });
      return send(res, 200, result.response);
    } catch (error) {
      return send(res, 400, { error: error.message });
    }
  }

  if (req.method === "POST" && url.pathname === "/v1/trust-index/evaluate") {
    try {
      const input = await readJson(req);
      return send(res, 200, evaluateTrustIndex(input));
    } catch (error) {
      return send(res, 400, { error: error.message });
    }
  }

  return send(res, 404, {
    error: "Not found.",
    availableEndpoints: [
      "GET /health",
      "GET /api/openapi.yaml",
      "GET /v1/protocols",
      "POST /v1/roof-damage/verify",
      "POST /v1/trust-index/evaluate"
    ]
  });
}

if (process.argv.includes("--smoke")) {
  const sample = JSON.parse(readFileSync(join(__dirname, "api/sample-request.json"), "utf8"));
  console.log(JSON.stringify(verifyRoofDamage(sample).response, null, 2));
} else {
  http.createServer(handle).listen(PORT, HOST, () => {
    console.log(`Roof Damage Verification API listening on http://${HOST}:${PORT}`);
  });
}
