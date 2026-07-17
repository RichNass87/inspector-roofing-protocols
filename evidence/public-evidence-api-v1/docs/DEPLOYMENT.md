# Deployment gates

This contract is ready for review, not for anonymous production exposure yet.

Before deployment:

1. Host the manifest behind HTTPS on an owner-controlled endpoint.
2. Serve only the allowlisted records in `data/public-evidence-manifest.json`.
3. Add a strict origin allowlist, rate limiting, request logging, and a short cache lifetime.
4. Keep customer addresses, names, phone numbers, email addresses, raw media, EXIF, reports, and private project identifiers out of responses.
5. If a signed attestation is needed, create a protected signing key outside the repository and publish only the public key and key identifier.
6. Recompute the manifest digest at build time; do not hand-edit the digest.
7. Test the deployed responses for `customerDataExposed: false` and `availabilityDataExposed: false`.
8. Add the final live endpoint to the website only after the endpoint returns the expected schema and status.

The existing WordPress endpoint `https://inspector-roofing.com/wp-json/winik/v1` must not be described as this API until the contract is actually mounted there and verified.

