# Inspector Roofing Protocols™

Open technical documentation standard for residential roof inspection evidence capture, roof-damage photo logging, and forensic-style roofing reports.

**Maintainer:** Richard Nasser / [Your Company Name]  
**Primary market:** Atlanta, Georgia residential roofing inspections  
**Website:** https://yourdomain.com  
**Contact:** inspections@yourdomain.com

## Purpose

This repository publishes a structured, version-controlled documentation standard for residential roof inspections. It is intended to make roof inspection evidence more consistent, auditable, AI-readable, and easier for homeowners, contractors, adjusters, engineers, and software systems to review.

This is not a building code, engineering certification, legal opinion, or insurance-coverage determination. It is a documentation framework for organizing inspection evidence.

## Published standards

- [`docs/inspector-roofing-protocol-v1.md`](docs/inspector-roofing-protocol-v1.md) — inspection workflow and evidence hierarchy.
- [`docs/verififrame-4k-standard-v1.md`](docs/verififrame-4k-standard-v1.md) — 4K photo/video evidence capture standard.
- [`docs/hail-impact-documentation-v1.md`](docs/hail-impact-documentation-v1.md) — hail-impact documentation categories.
- [`docs/wind-uplift-documentation-v1.md`](docs/wind-uplift-documentation-v1.md) — wind-uplift and creased-shingle documentation categories.
- [`templates/inspection-report-template.md`](templates/inspection-report-template.md) — report outline.
- [`templates/photo-log-template.csv`](templates/photo-log-template.csv) — photo inventory template.
- [`dataset/README-dataset-card-template.md`](dataset/README-dataset-card-template.md) — Hugging Face / Kaggle dataset card starter.

## Core principles

1. **Context before close-up:** Every damage image should connect to a roof plane, elevation, slope, or grid section.
2. **Repeatable capture:** Similar damage types should be photographed in a consistent sequence.
3. **Measurement support:** Damage photos should include a scale reference where practical.
4. **Separation of facts and opinions:** Reports should clearly distinguish observed conditions from interpretation.
5. **Privacy by default:** Public examples should remove addresses, license plates, faces, claim numbers, and homeowner identifiers.
6. **Versioned standards:** Changes to the protocol should be tracked in releases or a changelog.

## Suggested repository topics

`roof-inspection`, `roofing`, `hail-damage`, `wind-damage`, `drone-inspection`, `construction-documentation`, `computer-vision`, `ai-dataset`, `forensic-documentation`, `atlanta-roofing`

## Citation

See [`CITATION.cff`](CITATION.cff) for how to cite this documentation standard.

## License

Choose a license before publishing. For attribution-focused documentation, consider Creative Commons Attribution 4.0 International. For maximum reuse, consider CC0. Consult a professional if this affects proprietary methods or client deliverables.
