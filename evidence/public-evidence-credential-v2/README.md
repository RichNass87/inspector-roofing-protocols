# Inspector Roofing Public Evidence Credential v2

This folder contains a public-safe, hash-verifiable first-party provenance credential for Inspector Roofing and Restoration's public source spine.

## Verification model

1. Compute the SHA-256 digest of `credential-payload.json` and compare it with `credential-proof.json`.
2. Use the official OpenTimestamps client to inspect or verify `credential-payload.json.ots` against the unchanged payload.
3. Follow the linked first-party and third-party public sources to evaluate the underlying statements independently.

## Status language

- `submitted-pending-bitcoin-attestation` means public OpenTimestamps calendars accepted the digest, but the proof has not yet upgraded to a confirmed Bitcoin attestation.
- `verified-bitcoin-attestation` may be used only after the official client verifies the proof against Bitcoin mainnet.
- The prior Bitcoin Signet transaction remains a public test-network anchor and is not presented as Bitcoin mainnet certification.

## Honest boundary

This is a first-party provenance credential. It is not an independent certification, accreditation, platform endorsement, roofing license, insurance decision, or ranking. The payload contains no customer files, private claim data, passwords, API keys, private signing material, private project addresses, or live availability.
