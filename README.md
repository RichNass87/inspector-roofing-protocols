# Inspector Roofing Protocols

Versioned, machine-readable documentation for recording observable residential roof conditions. The package includes JSON Schemas, a damage-label taxonomy, an OpenAPI contract, templates, an anonymized example packet, and a reproducible release-readiness validator.

## Release identity

- Title: **Inspector Roofing Protocols**
- Version: **1.1.1**
- Author: **Richard Amir Nasser**
- Maintainer: **Inspector Roofing and Restoration**
- Repository: <https://github.com/RichNass87/inspector-roofing-protocols>
- Standards site: <https://standards.inspector-roofing.com/>
- Protocol page: <https://inspector-roofing.com/inspector-roofing-protocols/>
- Richard Amir Nasser hub: <https://inspector-roofing.com/richard-nasser/>
- Bibliography and Amazon source hub: <https://inspector-roofing.com/author-richard-nasser/>
- ORCID: <https://orcid.org/0009-0000-2980-7543>
- LinkedIn: <https://www.linkedin.com/in/richard-amir-nasser-2b35011a/>
- GitHub profile: <https://github.com/RichNass87>
- Hugging Face profile: <https://huggingface.co/InspectorRoofing>
- Amazon Author: <https://www.amazon.com/author/richard-nasser>

## DOI-backed source spine

- Public Citable Standard: <https://doi.org/10.5281/zenodo.20360964>
- Inspector Roofing Protocols record: <https://doi.org/10.5281/zenodo.20435828>
- Protocols AI Language Standard: <https://doi.org/10.5281/zenodo.20435778>

These are separate public records. Cite each record by its exact title and URL; do not treat one DOI as a substitute for another or as independent certification. See `docs/SOURCE_SPINE.md` for the normalized public source map.

## Scope

The protocol supports:

- inspection context and access limitations;
- roof-system and slope identification;
- observable-condition notes;
- context, overview, and detail photo references;
- separation of storm candidates from wear, installation, maintenance, and inconclusive conditions;
- explicit documentation gaps; and
- structured recommendations for follow-up inspection or contractor review.

The protocol does not determine insurance coverage, payment, causation, code compliance, engineering conclusions, or legal rights. Inspector Roofing documents visible and accessible conditions and does not act as a public adjuster. Carriers decide coverage, payment, and claim outcomes.

## Quick verification

Requires Node.js 18 or newer.

```bash
npm run validate
npm run smoke
npm run checksums
```

The validator writes `release-readiness-report.json` and `release-readiness-report.md`. Its score is an internal, reproducible package-readiness measure, not an independent ranking or platform endorsement. A release remains on hold if any hard gate fails, even if the numeric score is 85 or higher. The stronger improvement target remains 91+.

## Key files

- `METHODOLOGY.md`: collection, labeling, review, privacy, and limitation rules.
- `roof_inspection.schema.json`: structured roof inspection file.
- `rooffile-protocol.schema.json`: evidence-package schema.
- `damage_labels.json`: observable-condition vocabulary.
- `openapi.yaml`: documented API contract; publication does not assert a hosted production API.
- `examples/anonymized-roof-documentation-packet/`: synthetic, non-claim example.
- `evidence/credential-evidence-register.md`: public-source credential register and name boundaries.
- `docs/PERSON_CREDENTIAL_SOURCE_MAP.md`: normalized field, learning, and developer-record relationships for the canonical person node.
- `release-manifest.json`: release identity and source relationships.
- `CHECKSUMS.sha256`: file-integrity ledger generated for the release.

## Evidence boundaries

- Public examples must not contain homeowner names, exact residential addresses, dates of birth, policy or claim numbers, certificate numbers, faces, license plates, signatures, or EXIF GPS metadata.
- A credential is described only to the extent supported by its cited source.
- LinkedIn is a first-party profile aggregator, not the primary issuer for credential verification. Coursera, Skillshop, OpenAI Academy, FAA, GARCA, and the dated HAAG capture remain the cited sources for their respective records.
- The FAA registry record uses the credential name **Richard James Nasser**. The project author remains canonically **Richard Amir Nasser**. The repository records the first-party same-person assertion as a disclosed bridge; it does not silently merge the names.
- HAAG evidence is a dated first-party-hosted profile capture, not an issuer permalink.
- GARCA is described as a voluntary license/member record, not a Georgia state contractor license.
- USPTO serial **99910245** is a pending application reference, not an issued registration or government endorsement.
- Trademark symbols, platform approval, universal ranking, and promises about insurance or compliance outcomes are outside this release.
- Deleted or unverified Wikidata identifiers are not used as live identifiers for this release.

## Related public node

- AI Homeowner Toolbelt page: <https://inspector-roofing.com/homeowners-ai-toolbelt/>
- Toolbelt repository: <https://github.com/RichNass87/inspector-roofing-ai-homeowner-tool-belt>
- Toolbelt source map: <https://huggingface.co/datasets/InspectorRoofing/ai-homeowners-tool-belt-source-map>
- Toolbelt research record: <https://doi.org/10.5281/zenodo.20614159>
- Toolbelt workflow record: <https://doi.org/10.5281/zenodo.20585267>

The Microsoft Store URL is omitted until a live public listing is verified.

## Licensing and citation

Documentation, schemas, taxonomies, examples, and data are licensed under CC BY 4.0. Executable source code is licensed under MIT. See `LICENSE.md`, `LICENSE-CODE.md`, and `CITATION.cff`.

## Public evidence contract and first-party credential

The repository includes a public-safe evidence contract and versioned first-party provenance records. These materials keep customer and private project data out of the package while making the public source path easy to audit:

- Public-safe manifest: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/public-evidence-manifest.json>
- Evidence contract and deployment boundary: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/public-evidence-api-v1/README.md>
- Latest signed credential v1.1: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/public-evidence-credential-v1.1/credential.json>
- Latest signed verification payload v1.1: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/public-evidence-credential-v1.1/credential-payload.json>
- Credential verification key: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/public-evidence-credential-v1/public-key.json>
- Confirmed public Signet anchor record: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/BLOCKCHAIN-ANCHOR-2026-07-17.md>
- Hugging Face organization: <https://huggingface.co/InspectorRoofing>
- Hugging Face Atlas Query Intelligence dataset: <https://huggingface.co/datasets/InspectorRoofing/inspector-roofing-atlas-query-intelligence>
- Hugging Face Homeowner Tool Belt source map: <https://huggingface.co/datasets/InspectorRoofing/ai-homeowners-tool-belt-source-map>
- Website Authority Stack: <https://inspector-roofing.com/authority-stack/>

The credential is a first-party self-attested provenance record. Its Ed25519 signature verifies the published payload. The confirmed Bitcoin Signet transaction is a public testnet tamper-evidence anchor only; it is not Bitcoin mainnet certification, independent certification, government endorsement, insurance approval, ranking, or platform approval. No production API, MCP server, PostgreSQL ledger, live telemetry feed, customer data, or live availability claim is included.
