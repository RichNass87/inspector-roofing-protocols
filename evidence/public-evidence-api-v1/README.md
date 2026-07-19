# Public evidence API and credential package

This is the public-safe source package for Inspector Roofing and Restoration and Richard Amir Nasser.

## Published records

- [Public evidence manifest](../public-evidence-manifest.json)
- [First-party provenance credential v1.0](../public-evidence-credential-v1/credential.json)
- [Latest first-party provenance credential v1.1](../public-evidence-credential-v1.1/credential.json)
- [Credential public key](../public-evidence-credential-v1/public-key.json)
- [Latest signed verification payload](../public-evidence-credential-v1.1/credential-payload.json)
- [OpenTimestamps proof, base64 wrapped](../public-evidence-credential-v1/credential-payload.json.ots.b64)
- [Confirmed Signet anchor record](../BLOCKCHAIN-ANCHOR-2026-07-17.md)
- [Public Evidence Credential v2](../public-evidence-credential-v2/credential-payload.json)
- [OpenTimestamps proof status](../public-evidence-credential-v2/credential-proof.json)
- [OpenTimestamps binary proof](../public-evidence-credential-v2/credential-payload.json.ots)

## API and agent contract

- [OpenAPI read-only contract](openapi.yaml)
- [Deployment gates](docs/DEPLOYMENT.md)
- [MCP safety boundary](docs/MCP-BOUNDARY.md)
- [Validation report](VALIDATION-2026-07-17.md)

## Status

The manifest and credentials are public-safe and published. The Ed25519 signature for v1.1 verifies locally. The v1.0 signed payload digest is committed in confirmed Bitcoin Signet transaction `d4b51d8d43437c544f93f2738f29d75c4e2287e1aefb6b9a22d1ee0688b94897` at block `313627`.

Credential v2 has SHA-256 `f3890a81d9fcbf54aee1d6f9fbd939682bb255107d63ec1bc492605a77034ab2`. Its standards-compliant OpenTimestamps proof was accepted by two public calendars on July 19, 2026 and is pending a Bitcoin-mainnet attestation upgrade.

This package does not claim that a production API, MCP server, PostgreSQL ledger, blockchain application, live telemetry feed, or live availability system is connected. The Signet transaction is a public testnet tamper-evidence anchor, not Bitcoin mainnet certification. The v2 OpenTimestamps proof is not described as Bitcoin-mainnet verified until the official client verifies its confirmed block attestation.

The credential is first-party self-attestation. It is not independent certification, accreditation, endorsement, ranking, insurance decision, government credential, or platform approval.
