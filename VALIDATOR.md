# Release-readiness validator

The validator measures whether this repository is ready to be published as a coherent, public-safe, reproducible evidence package. It does not measure search rank, domain authority, professional skill, insurance outcomes, or third-party endorsement.

## Scoring

| Category | Points | Observable requirement |
| --- | ---: | --- |
| Identity and version consistency | 20 | Canonical title, author, organization, version, release date, and URLs agree across the core metadata. |
| Persistent source graph | 15 | Both DOI relationships, ORCID, repository, canonical person hub, and public Protocols page are present. |
| Methodology and sample | 15 | Methodology is complete; schemas/taxonomy parse; the synthetic sample is internally linked and deidentified. |
| Reproducibility and integrity | 15 | Runtime paths work; release manifest is complete; dual licensing is explicit; checksum ledger is current. |
| Privacy and claim boundaries | 20 | No restricted private fields or unresolved placeholders; insurance/public-adjuster boundary is present; overclaims are absent. |
| Credential evidence quality | 10 | GARCA, FAA, and HAAG source limits are recorded; the FAA name bridge and raw-card exclusion are explicit. |
| Published release | 5 | `release-manifest.json` is changed from `staged` to `published` only after the v1.1.0 public tag/release exists. |

Maximum staged score: 95. Maximum published score: 100. Release eligibility begins at 85; the stronger improvement target is 91+.

## Hard gates

The validator returns `RELEASE_HOLD` regardless of score if it finds:

- restricted personal data in the release files;
- an unresolved placeholder in a core file;
- missing insurance/public-adjuster boundary language;
- inconsistent canonical identity or version metadata;
- missing checksum coverage; or
- a score below 85.

`READY_FOR_SCHEDULED_RELEASE` means the staged package passed the internal checks. `PASS` means the manifest also records a published public release. Neither status is an independent certification.
