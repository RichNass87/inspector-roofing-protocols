# Inspector Roofing First-Party Provenance Credential v1

This folder contains a signed provenance statement for the public-safe evidence manifest.

## What can be verified

1. `credential-payload.json` is the exact payload that was signed.
2. `credential.json` records its SHA-256 digest and Ed25519 signature.
3. `public-key.json` contains the public verification key and its fingerprint.
4. `credential-payload.json.ots` is an OpenTimestamps proof for the payload digest.

## Honest status

- Signature: ready for Ed25519 verification.
- Bitcoin anchor: pending confirmation and proof upgrade.
- Issuer: first-party self-attestation.
- Independent certification: not claimed.
- Customer data: not included.
- Production API, MCP server, PostgreSQL ledger, live telemetry, and blockchain application: not claimed as connected.

## Why this is stronger than an unsigned claim

The signature makes later tampering detectable, and the Bitcoin timestamp can establish that the exact payload existed before a confirmed timestamp. Neither mechanism proves that every statement is true; source quality and independent issuer records still matter.

