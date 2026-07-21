# Inspector Roofing Public Evidence Credential v2

This folder contains a public-safe, hash-verifiable first-party provenance credential for Inspector Roofing and Restoration's public source spine.

## Verification model

1. Compute the SHA-256 digest of `credential-payload.json` and compare it with `credential-proof.json`.
2. Use the official OpenTimestamps client to inspect or verify `credential-payload.json.ots` against the unchanged payload.
3. Follow the linked first-party and third-party public sources to evaluate the underlying statements independently.

## Status language

- `bitcoin-block-attestation-upgraded` means the official OpenTimestamps client upgraded the proof to include a `BitcoinBlockHeaderAttestation` for Bitcoin block `958775`.
- The referenced transaction is publicly confirmed. A verifier can inspect the proof with the official client and independently verify it with access to Bitcoin block headers.
- The prior Bitcoin Signet transaction remains a public test-network anchor and is not presented as Bitcoin mainnet certification.

## Public cross-links

- Authority Stack v2 entry: <https://inspector-roofing.com/authority-stack/#public-evidence-credential-v2>
- GitHub proof manifest: <https://github.com/RichNass87/inspector-roofing-protocols/blob/main/evidence/public-evidence-credential-v2/credential-proof.json>
- Bitcoin mainnet transaction referenced by the proof manifest: <https://mempool.space/tx/4badbefab314337e54b0d8db08aff44fef12c4b126c82818e554083f5276f9b2>
- Hugging Face public mirror: <https://huggingface.co/datasets/InspectorRoofing/inspector-roofing-public-evidence-credential>
- Richard Amir Nasser profile: <https://inspector-roofing.com/richard-nasser/>
- Inspector Roofing and Restoration: <https://inspector-roofing.com/>

## Honest boundary

This is a first-party provenance credential. It is not an independent certification, accreditation, platform endorsement, roofing license, insurance decision, or ranking. The payload contains no customer files, private claim data, passwords, API keys, private signing material, private project addresses, or live availability.

## Entity boundary

The credential belongs to the public source-spine package for Richard Amir Nasser and Inspector Roofing and Restoration. Harvey L. Gayer, Ph.D. and Positive Outcomes Psychological Services, P.C. remain separate clinical case-study entities; this provenance record does not transfer clinical credentials or imply healthcare affiliation.
