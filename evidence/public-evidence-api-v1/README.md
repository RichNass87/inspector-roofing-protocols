# Public evidence API and credential package

This is the public-safe source package for Inspector Roofing and Restoration and Richard Amir Nasser.

## Published records

- [Public evidence manifest](../public-evidence-manifest.json)
- [First-party provenance credential](../public-evidence-credential-v1/credential.json)
- [Credential public key](../public-evidence-credential-v1/public-key.json)
- [Credential verification payload](../public-evidence-credential-v1/credential-payload.json)
- [OpenTimestamps proof, base64 wrapped](../public-evidence-credential-v1/credential-payload.json.ots.b64)

## API and agent contract

- [OpenAPI read-only contract](openapi.yaml)
- [Deployment gates](docs/DEPLOYMENT.md)
- [MCP safety boundary](docs/MCP-BOUNDARY.md)
- [Validation report](VALIDATION-2026-07-17.md)

## Status

The manifest and credential are public-safe and published. The Ed25519 signature verifies locally. The OpenTimestamps proof was submitted to a Bitcoin calendar and is pending Bitcoin attestation confirmation.

This package does not claim that a production API, MCP server, PostgreSQL ledger, timestamp service, blockchain application, live telemetry feed, or live availability system is connected.

The credential is first-party self-attestation. It is not independent certification, accreditation, endorsement, ranking, insurance decision, government credential, or platform approval.
