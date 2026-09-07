# cleanuprtx

Website and Google Search Console cleanup for Inspector Roofing.

Audits what Googlebot actually sees on every page, explains each structured-data
defect Search Console is reporting, repairs the ones it is allowed to touch as
WordPress **autosave revisions** you review and publish by hand, and tells you
exactly where to fix the rest.

## What it can and cannot change — by construction

The only thing cleanuprtx ever writes is a corrected `post_content`, and only
when it can prove the JSON-LD block it is fixing **lives in `post_content`**:
the block on the live page must match, byte for byte after whitespace, a block
in the REST `content.raw`. That single test is the write guard:

| Where the schema comes from | What happens |
|---|---|
| A `<script type="application/ld+json">` you pasted into the page body | **Repairable.** Patched and staged for your review. |
| Rank Math / Yoast (block carries their class, generated in `<head>` from plugin settings) | **Report-only.** The exact plugin screen to fix it is named. |
| Breakdance canvas, theme, or anything else not in `post_content` | **Report-only.** |
| Media attachments, author archives, Breakdance templates | Audited, never written. |

**Writes never touch the live page.** On a published (or private / pending /
scheduled) page the patched content goes to `POST /wp/v2/{type}/{id}/autosaves`,
which stores a revision the editor shows as *"There is an autosave of this post
that is more recent"*. The live URL, its status and its content are untouched
until you restore and publish. On a draft, the draft itself is updated. No
request ever carries a `status` field — the body is `{"content": ...}` and
nothing else, asserted in code and in tests.

Nothing is published, deleted, or written to post meta. Search Console access is
read-only: the token is requested with, and verified to carry, exactly the
`webmasters.readonly` scope.

**Credentials never leave the Keychain.** Every secret is read from the macOS
login Keychain at run time. Nothing is stored in this package, cached to disk,
printed, or placed on a command line where `ps` could see it.

## Setup — first evening

Nothing to install. Standard library only, Python 3.9+, from the checkout:

```sh
cd cleanuprtx
python3 -m cleanuprtx doctor
```

`doctor` lists which Keychain items are missing and the command that creates
each one. Three credential flows:

```sh
# 1. WordPress: an application password for each site
#    (WP admin > Users > Profile > Application Passwords > name it "cleanuprtx")
python3 -m cleanuprtx auth wordpress --site inspector-roofing

# 2. Google: a Desktop-app OAuth client
#    console.cloud.google.com > APIs & Services > Library > enable "Google Search Console API"
#    > Credentials > Create credentials > OAuth client ID > Desktop app
python3 -m cleanuprtx auth google-client      # prompts for the client ID and secret

# 3. Google: sign in once; a browser opens, you allow read-only access
python3 -m cleanuprtx auth google
```

Then `python3 -m cleanuprtx doctor` again. It authenticates against every site,
tells you which SEO plugin, page builder and security plugin are installed, and
checks that each configured Search Console property is visible to your Google
account with a permission level that allows URL inspection.

If you want a `cleanuprtx` command on your PATH: `pipx install .` from this
directory. Avoid a bare `pip install -e .` — Apple's stock Python ships a pip
too old for it, and Homebrew's refuses to install outside a virtualenv.

### If doctor fails on WordPress

`doctor` distinguishes the three common causes and prints the fix:

- **Host strips the Authorization header** — add to `.htaccess`:
  `SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1`
- **Application passwords disabled** — Wordfence > All Options > turn off
  *Disable WordPress application passwords*
- **Credential rejected** — re-create the application password and re-run `auth wordpress`

### If Google says `invalid_grant` later

While the OAuth consent screen is in *Testing*, Google expires refresh tokens
after seven days. Run `auth google` again, or publish the consent screen
(Google Cloud > OAuth consent screen > Publish app) to make tokens long-lived.

## Use

```sh
# Audit the whole site the way Google sees it. Read-only.
python3 -m cleanuprtx audit --json audit.json

# Same, and record repairable findings as pending repairs
python3 -m cleanuprtx audit --json audit.json --propose

# Everything that must be fixed in Rank Math / the builder, grouped by screen
python3 -m cleanuprtx hand-edits audit.json

# Review, decide, preview, stage
python3 -m cleanuprtx pending
python3 -m cleanuprtx approve 342e4b4f 8ade232c
python3 -m cleanuprtx reject fe36879d
python3 -m cleanuprtx apply --dry-run
python3 -m cleanuprtx apply
```

After `apply`, each staged repair prints its edit link. Open it, restore the
autosave, check the page, publish.

Audit anything that is not in WordPress — the standards subdomain, which the
`sc-domain:inspector-roofing.com` property also covers:

```sh
python3 -m cleanuprtx audit --url https://standards.inspector-roofing.com/ --show-schema
python3 -m cleanuprtx audit --html ../docs/index.html
```

### Diagnosing the 403s on positive-outcomes.com and pnagolfcarts.com

```sh
# Locally: fetch as a browser and as three Google crawlers, compare
python3 -m cleanuprtx forbidden --site pnagolfcarts

# Authoritatively: what Google's own last crawl saw
python3 -m cleanuprtx indexing --site pnagolfcarts https://pnagolfcarts.com/
```

`forbidden` reads `robots.txt`, follows its `Sitemap:` lines, and flags any
URL that answers the browser with 200 but a Google crawler with 401/403/429 —
that pattern is a server, WAF or security-plugin rule keyed on the User-Agent,
not a WordPress setting. It also prints `server` / `cf-ray` / `x-wf-blocked`
headers so you can see whether Cloudflare or Wordfence is answering.

Because a Googlebot User-Agent from a home IP is itself treated as a fake bot
by Cloudflare and Wordfence, the `indexing` command is the confirmation: its
`fetch` line is `pageFetchState` from Google's real crawler. `ACCESS_FORBIDDEN`
there is the definitive answer.

`indexing --from-search-console` inspects every URL that had impressions in
the last 90 days. URL Inspection is limited to 2,000 requests per property per
day; the tool stops the moment Google answers 429 rather than burn the quota.

## Rules

| Rule | Severity | Google's wording | Repair |
|---|---|---|---|
| `profile-parent-node` | critical | *Invalid object type for field "\<parent_node\>"* | `mainEntity` → reference to the canonical Person if that node exists on the page, otherwise an embedded Person node |
| `object-field-type` | warning | *Invalid object type for field "creator"* | A name → `{"@type": "Person"/"Organization", "name"}`; a URL matching a node on the page → `{"@id"}`; an unknown URL or unresolvable `@id` → report only |
| `invalid-datetime` | warning | *Invalid datetime value for "dateModified"* | `dateModified` ← the page's `modified_gmt`; `datePublished` ← `date_gmt`; both with explicit UTC offset |
| `entity-fragmentation` | warning | — (splits the knowledge graph) | Rename the node's `@id` **and every reference to it** in the block. Fires only for nodes that name or link to the canonical person/organization; other people are left alone |
| `invalid-jsonld` | critical | — | Report only |
| `stale-draft` | notice | — | Report only. On Breakdance sites judged by title and edit history, not body length |

`object-field-type` checks that the value is a Person/Organization object or a
reference that resolves to one *on the same page*; `mainEntity` is checked here
on ordinary pages and by `profile-parent-node` on a `ProfilePage`, never both.

Canonical identities live in `config.py` per site. Only inspector-roofing has
them; the other two sites are never asked to merge anyone.

## Ledger

Approvals live in `~/.cleanuprtx/approvals.json` (`0600`, directory `0700`,
written atomically). Repair IDs are content-hashed over site, type, page, rule,
the node's `@id` and the patch, so re-running an audit never renumbers a pending
decision and two identical defects on different nodes never collide. Repairs
whose target vanished are marked `stale`; failed applies stay in the queue and
are retried on the next `apply`.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

95 tests, no network and no Keychain. They include every write-path guard (the
body is only ever `{"content"}`, published pages go to `/autosaves`, media and
Breakdance templates are refused, a block that is not in `post_content` is
refused), the three WordPress auth diagnoses, Search Console response parsing
and scope enforcement, and a regression check that this repository's own
JSON-LD stays clean.

## Scope

cleanuprtx does not publish, does not delete, does not change any page's status,
does not write post meta or plugin settings, does not edit Breakdance canvases,
and does not submit anything to Google. Those stay manual on purpose.
