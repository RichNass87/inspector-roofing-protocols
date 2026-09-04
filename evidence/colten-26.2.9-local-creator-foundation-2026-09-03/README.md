# Colten 26.2.9 Local Creator Foundation Install Record

Status date: 2026-09-03  
Record published: 2026-09-04  
Host: Mac Studio (Apple silicon, arm64)  
Canonical person: Richard Amir Nasser  
Canonical organization: Inspector Roofing and Restoration

## Interpretation Boundary

This record preserves a first-party install and verification summary for a locally installed candidate build of Colten 26.2.9. It documents what was checked on the Mac Studio and what remains disabled. It does not claim a public release, an app-store listing, notarized distribution, third-party certification, or that the complete Ultimate Creator execution system is available.

Colten 26 is recorded here only as the locally installed Creator application on the Mac Studio. The record does not assert authorship, publisher, licensing, or distribution rights for Colten beyond the local install, and it does not connect the install to any roofing credential, license, insurance record, or customer outcome.

The DMG, install report, and install receipt are local files. They are listed for traceability and are not published in this repository. Their SHA-256 checksums are recorded in the local install report and are not reproduced in this record. No digest for those files should be cited from this directory until it has been copied from the report.

No Keychain owner credential, API key, provider credential, signing key, or paid-provider account detail is included.

## Verified on 2026-09-03

- `/Applications/Colten 26.app` reports version 26.2.9 (arm64).
- Core health returns HTTP 200 and version 26.2.9.
- All five previously loaded Colten services were restored.
- Creator status, inventory, and contracts routes return HTTP 200 with the Keychain owner credential and HTTP 401 without it.
- Rollback receipt and runtime preservation checks passed.
- Full Core suite: 1,896 passed, 4 skipped, 2 expected failures, plus 1,093 subtests.
- Installer audit: no remaining release blocker. The audit was a separate pass from the install run; it is not third-party certification.

## Local Artifacts (Mac Studio, not published)

Collection root: the 2026-09-03 install output directory on the Mac Studio. The absolute path is not published, because it carries a host account identifier, and `METHODOLOGY.md` requires account identifiers to be removed from public records. The files below are named relative to that collection root.

| File | Role | SHA-256 in this record |
| --- | --- | --- |
| `Colten-26.2.9-local-candidate-aarch64.dmg` | Local candidate installer image | Not imported; see the local install report |
| `Colten-26.2.9-install-report.md` | Full install report with checksums and the exact remaining activation work | Not imported; see the local install report |
| `Colten-26.2.9-install-receipt.json` | Machine-readable install receipt | Not imported; see the local install report |

## Current Capability Boundary

This is the installed Creator foundation, not the complete Ultimate Creator execution system. The live Creator status reports inventory-and-contracts-only.

| Component | Reported state on 2026-09-03 |
| --- | --- |
| Creator surface | Inventory and contracts only |
| Unreal 5.8.2 | Detected; automated launching, rendering, and round trips not enabled |
| Final Cut Pro 12.3 | Detected; automated launching, rendering, and round trips not enabled |
| Logic Pro 12.3.1 | Detected; automated launching, rendering, and round trips not enabled |
| Reality Composer Pro 3.0 | Detected; automated launching, rendering, and round trips not enabled |
| Xcode 27 betas | Detected; automated launching, rendering, and round trips not enabled |
| Instant360 | Not registered |
| RTX workers | Live attestation still required |
| Inventoried models (127) | Not selectable through the Creator surface |
| Paid provider credits | Not exercised during this install |

## Creative and Developer Toolchain Inventory Frame

The five detected applications are expanded into per-class inventory slots in [creative-toolchain-inventory.json](./creative-toolchain-inventory.json). The classes are Unreal, developer assets and build tooling, 3D, Final Cut Pro, and Logic Pro.

Detection means the application was present on the Mac Studio on 2026-09-03. It does not mean the application was launched, driven, rendered from, or integrated with Colten, and it does not mean that any of its content was read.

| Class | Host application on 2026-09-03 | Automated | Asset inventory state |
| --- | --- | --- | --- |
| Unreal | Unreal 5.8.2, detected | No | Not collected |
| Developer assets and build tooling | Xcode 27 betas, detected | No | Not collected |
| 3D | Reality Composer Pro 3.0, detected | No | Not collected |
| Final Cut Pro | Final Cut Pro 12.3, detected | No | Not collected |
| Logic Pro | Logic Pro 12.3.1, detected | No | Not collected |

Not collected means that no export has been run for that class, so nothing is known about its contents. It does not mean that the class is empty. The record keeps that state separate from collected-empty, which is reserved for an export that genuinely returns zero items.

The 127 inventoried models are carried separately and unclassified. The reported boundary states the count and states that the models are not selectable through the Creator surface. No breakdown of the 127 has been imported into this record. This record therefore does not assign them to the 3D class or to any other class, and does not assert what kind of model they are.

Two further components are recorded without an established state. The RTX workers still require live attestation. Instant360 is not registered. This record does not state what either one does, and neither is connected to any class above.

The record also carries the per-class import requirements, so that the export at the Mac Studio collects the right fields in the right units. If the export finds projects, libraries, events, or referenced assets, their names and file paths may carry customer names, property addresses, or claim identifiers. Each must be reviewed against the repository privacy rules in [METHODOLOGY.md](../../METHODOLOGY.md) before it is copied into the record. Absolute paths are not published: a path is recorded only as a form relative to a stated collection root, with any customer, property, or claim segment removed.

## Remaining Activation Work

The local install report lists the exact remaining activation work. This record summarizes the reported boundary and does not replace that list.

1. Enable automated launching, rendering, and round trips for the detected creative applications.
2. Register Instant360.
3. Complete live attestation for the RTX workers.
4. Make the 127 inventoried models selectable through the Creator surface.
5. Decide whether paid provider credits are exercised in a controlled test before any provider-backed capability is described as verified.
6. Import the SHA-256 checksums from the install report into this record so the local artifacts can be matched byte for byte.

Two further items arise from the toolchain inventory frame and are not part of the install report list: run the per-class export on the Mac Studio and populate the Unreal, developer assets and build tooling, 3D, Final Cut Pro, and Logic Pro inventory slots; and record what kind of model the 127 inventoried models are, so the count can be classified.

## Boundary

Install verification establishes that the recorded build was installed, responded to the recorded checks, and passed the recorded test suite on one machine on one date. It does not establish that every Colten capability works, that the detected creative applications are integrated, that any inventoried model is available for selection, or that any paid provider path has been exercised. Public documentation of this record establishes availability and version history only; it is not endorsement, certification, ranking, insurance approval, or a customer outcome.
