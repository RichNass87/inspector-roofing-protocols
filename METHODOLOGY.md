# Inspector Roofing Protocols methodology

Version 1.1.0, released 2026-07-16.

## Purpose

Inspector Roofing Protocols defines a repeatable structure for recording visible and accessible roof conditions. It is designed for traceable field documentation, not for deciding insurance, legal, engineering, manufacturer-warranty, or code outcomes.

## Collection sequence

1. Record inspection date, general city/region, roof system, access method, weather at inspection, and access limitations.
2. Capture property and roof-plane context before detail images.
3. Assign a stable observation ID and photo ID before making a recommendation.
4. Describe what is visible without assuming cause when the evidence does not establish cause.
5. Record possible alternative explanations such as age, wear, installation, manufacturing, maintenance, or mechanical contact.
6. Mark weak, missing, or conflicting documentation as a gap rather than converting uncertainty into a conclusion.
7. Separate observations from recommendations and preserve the link between each recommendation and its supporting observation IDs.

## Evidence levels

- **Context:** establishes the roof plane, elevation, surrounding field, and access conditions.
- **Detail:** shows the specific observable condition with scale when practical.
- **Corroborating:** shows related collateral, pattern, interior, or adjacent conditions.
- **Limitation:** records an inaccessible area, missing context, image-quality defect, or other reason the observation cannot support a stronger conclusion.

The protocol does not assign a probability of insurance coverage. Terms such as `hail_candidate` and `wind_candidate` are documentation labels, not coverage or causation determinations.

## Review rules

- A reviewer must be able to trace every finding to an observation and, when available, to one or more media IDs.
- Conflicting evidence is retained and described.
- A missing photo, date, scale, location, or roof-plane context is reported as a documentation gap.
- Automated output is advisory and must not be described as an engineering, legal, code, manufacturer, or insurance decision.
- Changes to the schema, taxonomy, or boundary language require a version and changelog entry.

## Privacy and redaction

Public examples must be synthetic or deidentified. Remove homeowner names, exact residential addresses, dates of birth, certificate numbers, signatures, policy and claim numbers, account identifiers, faces, license plates, and EXIF GPS metadata. Do not publish third-party documents without permission and a documented redaction review.

The supplied FAA card image is excluded from this public package because it contains private address, birth-date, certificate-number, and signature fields. The public FAA Airmen Inquiry is used instead.

## Credential handling

Credential records are not used to infer qualifications beyond the source wording. Issuer records are preferred. First-party copies are labeled as first-party copies. Where source-display names differ from the canonical public identity, the difference and the basis of any same-person bridge are disclosed rather than silently merged.

## Insurance and public-adjuster boundary

Inspector Roofing and Restoration documents observable conditions and provides roofing inspection and contracting information. It does not act as a public adjuster. Carriers decide coverage, payment, and claim outcomes. Nothing in this package is legal advice or an interpretation of a policy.

## Reproducibility

Run `npm run checksums`, `npm run validate`, and `npm run smoke` from the repository root. The checksum ledger excludes Git internals, generated readiness reports, and the checksum file itself. The validator criteria are defined in `VALIDATOR.md`; a numeric result is an internal package-readiness score, not a third-party ranking.
