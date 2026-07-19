# Inspector Roofing Public Evidence Credential v2

This folder contains a public-safe, hash-verifiable first-party provenance credential for Inspector Roofing and Restoration's public source spine.

## Public mirrors

- [GitHub credential files](https://github.com/RichNass87/inspector-roofing-protocols/tree/main/evidence/public-evidence-credential-v2)
- [Hugging Face dataset mirror](https://huggingface.co/datasets/InspectorRoofing/inspector-roofing-public-evidence-credential)
- [Inspector Roofing Authority Stack](https://inspector-roofing.com/authority-stack/)

## Verification model

1. Compute the SHA-256 digest of `credential-payload.json` and compare it with `credential-proof.json`.
2. Use the official OpenTimestamps client to inspect or verify `credential-payload.json.ots` against the unchanged payload.
3. Follow the linked first-party and third-party public sources to evaluate the underlying statements independently.

## Status language

- `bitcoin-transaction-created-waiting-confirmations` means the official OpenTimestamps client reports that the digest has been timestamped by a Bitcoin mainnet transaction, but the proof is still waiting for sufficient confirmations before it can be upgraded and verified against a Bitcoin block attestation.
- `verified-bitcoin-attestation` may be used only after the official client verifies the proof against Bitcoin mainnet.
- The prior Bitcoin Signet transaction remains a public test-network anchor and is not presented as Bitcoin mainnet certification.

## Honest boundary

This is a first-party provenance credential. It is not an independent certification, accreditation, platform endorsement, roofing license, insurance decision, or ranking. The payload contains no customer files, private claim data, passwords, API keys, private signing material, private project addresses, or live availability.
