# Changelog

All notable changes are recorded here. Dates use ISO 8601.

## [1.1.1] - 2026-07-16

### Fixed

- Removed 19 missing or deleted Wikidata identifiers after an official API check.
- Corrected The Roofing Search Integrity Report to the verified live item `Q140522693`.
- Replaced the retired Q-link grid with ORCID, GitHub, DOI, Hugging Face, and verified-source links.
- Added a validator hard gate that allows only the verified live Wikidata item and blocks retired Q IDs from release files.

## [1.1.0] - 2026-07-16

### Added

- Reproducible 100-point release-readiness validator with hard release gates.
- Versioned release manifest and SHA-256 checksum generator.
- Methodology, evidence limitations, and insurance/public-adjuster boundary.
- Synthetic anonymized roof-documentation packet.
- Public-safe credential evidence register covering GARCA, FAA Part 107, and HAAG source limits.
- Explicit credential-name bridge between canonical author Richard Amir Nasser and the FAA record name Richard James Nasser.

### Changed

- Normalized project title, author, organization, repository, DOI relationships, version, and licensing metadata.
- Replaced placeholder URLs and source-display author names in active protocol files.
- Corrected runtime paths so the local smoke test uses the repository files that are actually shipped.
- Limited API language to a published contract rather than an assertion of a live production service.

### Security and privacy

- Excluded the supplied FAA card image because it contains private address, birth-date, certificate-number, and signature fields.
- Added automated scans for private identifiers, unresolved placeholders, overclaims, and missing claim boundaries.

## [1.0.2] - 2026-06-29

- Added DOI-backed citation metadata and related roofing visibility research.

## [0.1.0]

- Initial JSON schema, damage-label taxonomy, templates, and CLI utilities.
