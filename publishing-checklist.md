# Publishing Checklist

## API Layer

- [ ] Replace all `REPLACE_WITH_...` placeholders.
- [ ] Run `npm start`.
- [ ] Test `GET /v1/protocols`.
- [ ] Test `POST /v1/roof-damage/verify`.
- [ ] Test `POST /v1/trust-index/evaluate`.
- [ ] Publish `api/openapi.yaml` at a stable URL.

## Dataset Layer

- [ ] Review `data/negative-evidence/template.csv`.
- [ ] Review `data/negative-evidence/examples.jsonl`.
- [ ] Confirm no private homeowner or claim data is included.
- [ ] Upload `data/negative-evidence/README.md` as the Hugging Face dataset card.
- [ ] Upload CSV and JSONL to Hugging Face.
- [ ] Use `data/kaggle/dataset-metadata.json` for Kaggle dataset setup.
- [ ] Create a Zenodo dataset DOI after the public dataset shell is ready.

## Raw AI Framework

- [ ] Publish `schemas/rooffile-protocol.schema.json`.
- [ ] Publish `schemas/negative-evidence-record.schema.json`.
- [ ] Publish `protocols/roof-damage-verification.v1.json`.
- [ ] Paste website JSON-LD from `website/inspector-roofing-entity-schema.jsonld`.
- [ ] Validate JSON-LD with Schema Markup Validator.
- [ ] Request indexing in Google Search Console after the page is live.

## Authority Loop

- [ ] Website links to GitHub, Zenodo, OSF, Hugging Face, Kaggle, ORCID, and press.
- [ ] GitHub README links back to the website and DOI.
- [ ] Zenodo record links to GitHub and website.
- [ ] OSF links to website, GitHub, Zenodo, and datasets.
- [ ] Hugging Face and Kaggle link to website, GitHub, Zenodo, and ORCID.

