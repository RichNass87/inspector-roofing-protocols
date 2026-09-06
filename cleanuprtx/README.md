# cleanuprtx

Website and Google Search Console cleanup for Inspector Roofing.

Audits WordPress content against the structured-data rules Google actually
enforces, proposes repairs one at a time, and writes only what you approve —
as drafts, never published.

## Design

**Credentials never leave the Keychain.** Every secret is read from the macOS
login Keychain at run time via `/usr/bin/security`. Nothing is stored in this
package, cached to disk, or printed. Error messages name the missing item, not
its value.

**Nothing is written without per-repair approval.** Findings become numbered
repairs. You approve them individually by ID. `apply` touches only approved
repairs, forces `status=draft`, and raises if anything asks it to publish.

**Breakdance pages are read but never rewritten.** Breakdance stores its canvas
in post meta, so the REST `content` field is a rendered artifact — writing to it
would be silently discarded on the next builder save. Defects on those pages are
reported and marked `[breakdance]` for hand-editing.

**Search Console access is read-only.** cleanuprtx requests the `webmasters.readonly`
scope. It reports what Google sees; it never submits validation requests or
removals on your behalf.

**No dependencies.** Standard library only, Python 3.9+. Nothing to install.

## Setup

Store four credentials once:

```sh
security add-generic-password -s cleanuprtx-wp-app-password \
    -a richard@inspector-roofing.com -w
security add-generic-password -s cleanuprtx-google-client-id -w
security add-generic-password -s cleanuprtx-google-client-secret -w
security add-generic-password -s cleanuprtx-google-refresh-token -w
```

Each prompts for the value without echoing it. The WordPress credential is an
**application password** (Users → Profile → Application Passwords), not your
login password — it is scoped to the REST API and revocable on its own.

Then install and verify:

```sh
pip install -e .
cleanuprtx doctor
```

`doctor` reports which items are present, authenticates against both services,
and lists the Search Console properties your token can read.

## Use

```sh
# Scan. Read-only, writes nothing.
cleanuprtx audit --site inspector-roofing

# Scan and record auto-fixable findings as pending repairs.
cleanuprtx audit --propose --json audit.json

# Review what is waiting.
cleanuprtx pending

# Decide, individually.
cleanuprtx approve 342e4b4f 8ade232c
cleanuprtx reject fe36879d

# See exactly what would be written.
cleanuprtx apply --dry-run

# Write the approved repairs as drafts.
cleanuprtx apply
```

Two diagnostics that don't touch content:

```sh
# What does Google currently know about these URLs?
cleanuprtx indexing https://inspector-roofing.com/richard-nasser/

# Is the server filtering Googlebot by user agent?
cleanuprtx forbidden --site pnagolfcarts
```

`forbidden` fetches each URL twice — once as a browser, once as Googlebot — and
flags any URL that answers 200 to one and 403 to the other. That pattern means a
server or firewall rule, not a WordPress setting.

## Rules

| Rule | Severity | Catches |
|---|---|---|
| `profile-parent-node` | critical | `ProfilePage` with a missing or string `mainEntity`. Google reports this as *Invalid object type for field "&lt;parent_node&gt;"*. |
| `object-field-type` | warning | `creator`, `author`, `publisher`, `mainEntity`, `founder` set to a bare string. Reported as *Invalid object type for field "creator"*. |
| `invalid-datetime` | warning | `dateModified`, `datePublished`, `dateCreated` that are not ISO 8601. |
| `entity-fragmentation` | warning | A `Person` or `Organization` node using a non-canonical `@id`, which makes Google treat one entity as two. |
| `empty-draft` | notice | Drafts under 200 characters of body text. |

Canonical IDs are declared in `config.py`:

- Person — `https://inspector-roofing.com/richard-nasser/#person`
- Organization — `https://inspector-roofing.com/#organization`

## Ledger

Approvals live in `~/.cleanuprtx/approvals.json`, mode `0600`. Repair IDs are
content-hashed, so re-running an audit does not renumber anything and decisions
survive between runs.

## Tests

```sh
python3 -m unittest discover -s tests
```

18 tests, no network and no Keychain access.

## Scope

cleanuprtx does not publish, does not delete, does not edit Breakdance canvases,
and does not submit anything to Google. Those stay manual on purpose.
