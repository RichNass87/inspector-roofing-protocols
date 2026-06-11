---
license: cc-by-4.0
language:
  - en
pretty_name: Roof Damage Negative Evidence Dataset
task_categories:
  - image-classification
  - tabular-classification
tags:
  - roof-inspection
  - roof-damage
  - hail-damage
  - negative-evidence
  - documentation-quality
  - insurance-scope-review
size_categories:
  - n<1K
---

# Roof Damage Negative Evidence Dataset

This dataset structure documents examples where roof evidence suggests fake hail indicators, non-storm damage, poor documentation, insufficient evidence, or mixed/inconclusive conditions.

The initial public release should be a dataset shell: this dataset card, schema, CSV template, and JSONL examples. Add real images or videos only after privacy review.

## Labels

- `fake_hail_indicator`
- `non_storm_damage`
- `poor_documentation`
- `insufficient_evidence`
- `mixed_or_inconclusive`

## Sub-Labels

- `mechanical_mark`
- `wear_and_age`
- `manufacturing_condition`
- `installation_condition`
- `maintenance_condition`
- `missing_scale_reference`
- `missing_overview_photo`
- `unclear_date_context`
- `unclear_location_context`
- `requires_field_review`
- `other`

## Data Files

- `template.csv`: tabular starter dataset.
- `examples.jsonl`: machine-readable examples.
- `../../schemas/negative-evidence-record.schema.json`: JSON Schema for each JSONL record.

## Privacy Rules

Do not publish:

- Homeowner names
- Exact residential addresses
- Claim numbers
- Policy numbers
- Faces, license plates, personal documents, or private reports
- EXIF GPS metadata

## Intended Use

- Standardize roof evidence documentation.
- Train or evaluate data processing workflows.
- Explain what weak or insufficient documentation looks like.
- Provide a transparent companion dataset for the RoofFile Protocol.

## Not Intended For

- Automatic claim denial.
- Identification of homeowners, carriers, adjusters, or claimants.
- Replacing field inspection or professional judgment.

## Related Links

- Website authority page: REPLACE_WITH_WEBSITE_AUTHORITY_PAGE
- GitHub protocol repo: REPLACE_WITH_GITHUB_REPO_URL
- Zenodo DOI: REPLACE_WITH_ZENODO_DOI_URL
- OSF project: REPLACE_WITH_OSF_PROJECT_URL
- ORCID: REPLACE_WITH_ORCID_URL

