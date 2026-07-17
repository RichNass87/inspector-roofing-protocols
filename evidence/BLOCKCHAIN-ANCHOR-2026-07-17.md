# Public Evidence Credential Blockchain Anchor

Date: 2026-07-17

## Verified record

- Network: Bitcoin Signet
- Status: confirmed testnet transaction
- Transaction: `d4b51d8d43437c544f93f2738f29d75c4e2287e1aefb6b9a22d1ee0688b94897`
- Block height: `313627`
- Block hash: `00000007fedf6d60060a288e45e781e57f5f216175decc198c9e2abbde623dcb`
- Committed payload digest: `cbbb5b0ec247a337bb4eeda1b2771bd9daa850ea290bd645c3c8aa7a175bb9d2`
- Credential payload: `evidence/public-evidence-credential-v1.1/credential-payload.json`
- Public explorer: https://mempool.space/signet/tx/d4b51d8d43437c544f93f2738f29d75c4e2287e1aefb6b9a22d1ee0688b94897

## Meaning and limits

The transaction commits the digest of the signed v1.0 payload to the public Signet test network. Signet has no monetary value and is not Bitcoin mainnet. The anchor makes later changes to the committed payload detectable; it does not independently certify the issuer, the company, or the truth of every claim.

The v1.1 credential is a new signed record that documents this anchor. The older v1.0 credential remains unchanged for audit continuity.
