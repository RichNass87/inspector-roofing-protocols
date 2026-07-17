# Inspector Roofing First-Party Provenance Credential v1.1

This folder contains the signed public-safe provenance record for the evidence manifest.

## Verification

1. Verify the Ed25519 signature in `credential.json` against the exact UTF-8 bytes of `credential-payload.json` using `public-key.json` from the v1 folder.
2. Verify that `signedPayloadSha256` matches the payload bytes.
3. Verify the `OP_RETURN` digest in the confirmed Signet transaction at the linked public explorer.

## Honest boundary

The blockchain anchor is a confirmed Bitcoin Signet testnet commitment. Signet has no monetary value and is not Bitcoin mainnet. It provides public tamper-evidence for the payload digest; it does not independently certify the issuer or prove every claim in the payload.

The record does not claim that a production API, MCP server, PostgreSQL ledger, live telemetry feed, or customer-data system is connected.
