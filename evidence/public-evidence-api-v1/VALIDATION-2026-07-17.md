# Public Evidence Contract Validation

Date: 2026-07-17

## Result

The public-safe manifest passed validation with 10 unique records.

- HTTPS source URLs: passed
- Required record fields: passed
- Duplicate IDs: none
- Customer data flag: `false`
- Private project data flag: `false`
- Live availability flag: `false`
- Manifest status: explicitly unsigned
- Canonical SHA-256 digest: `d03a814132f10af77a2ffb63e090de8863e4528281302d2a5a9f2441bd7dd7f1`

## Credential verification

The v1.1 Ed25519 signature verifies against the exact UTF-8 payload bytes.

- Credential payload SHA-256: `335a58d0c72e4842acb369a3e9166f15c6ce791c0344aa1cec6a673dd18bce5f`
- Confirmed Signet transaction: `d4b51d8d43437c544f93f2738f29d75c4e2287e1aefb6b9a22d1ee0688b94897`
- Confirmed block: `313627`
- Committed v1.0 payload digest: `cbbb5b0ec247a337bb4eeda1b2771bd9daa850ea290bd645c3c8aa7a175bb9d2`

Signet is a public test network with no monetary value. This is not Bitcoin mainnet certification and does not independently prove the credential claims.

## Deployment boundary

The API and MCP files are contracts for a future read-only deployment. They are not represented as live endpoints yet. The existing WordPress evidence workflow remains the current live system; the PostgreSQL ledger, signing-key service, timestamp service, and live telemetry pipeline remain unconnected.

## Safe next gate

Deploy only after an owner-controlled endpoint is provisioned, rate-limited, tested for data leakage, and verified to return the same public-safe manifest. Then add the final endpoint URL to the website source map.
