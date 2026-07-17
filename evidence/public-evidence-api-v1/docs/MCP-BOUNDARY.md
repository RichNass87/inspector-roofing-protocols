# Safe MCP boundary

If this contract is later exposed through an MCP server, the initial tools should be read-only:

- `get_public_source_spine`
- `get_public_evidence_record`
- `get_public_roofing_education`

The server must reject or omit:

- customer names, addresses, phone numbers, email addresses, or report files;
- raw roof imagery or EXIF metadata;
- insurance coverage, payment, approval, or denial predictions;
- live crew availability or scheduling claims unless backed by a real transactional system;
- writes to WordPress, Google Business Profile, GitHub, Hugging Face, or Wikidata;
- arbitrary URL fetching and arbitrary code execution.

Every response should include its source URL, publication state, retrieval timestamp, and the claim boundary from the manifest.

