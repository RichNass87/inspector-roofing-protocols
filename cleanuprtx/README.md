# cleanuprtx

Website and Google Search Console cleanup for Inspector Roofing.

Audits what Googlebot actually sees on every page, explains each structured-data
defect Search Console is reporting, repairs the ones it is allowed to touch as
WordPress **autosave revisions** you review and publish by hand, and tells you
exactly where to fix the rest.

## What it can and cannot change — by construction

The only thing cleanuprtx ever writes is a corrected `post_content`, and only
when it can prove the JSON-LD block it is fixing **lives in `post_content`**:
the block on the live page must match a block in the REST `content.raw` — byte
for byte after whitespace between tokens, or failing that as the same parsed
JSON document (key order and string escaping such as `<\/` may differ). Blocks
inside HTML comments never count. That test is the write guard:

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
request ever carries a `status` field. The autosave body is the patched
`content` plus the page's own current `title` and `excerpt`, echoed unchanged
from the same read — WordPress stores an autosave with exactly the fields it is
given, and restoring one with an empty title would blank the page. A draft
update sends `content` only. Both are asserted in code and in tests.

**One write per page, across runs.** WordPress keeps a single autosave per
page per user, so every approved repair on a page is folded into one patched
`post_content` and staged with one request — and a later run re-carries the
repairs it staged earlier and you have not yet restored, so approving repairs
one at a time never loses one. Before re-carrying, `apply` re-runs the audit
rules on the page as it is now: a repair the rules no longer report has been
absorbed (you restored and published it, or fixed it by hand) and is marked
so — but only if the page was saved since staging, since an autosave never
changes `modified_gmt` and an unsaved page cannot have absorbed anything. One
they still report is re-carried with the patch the rules build now, found by
the defect itself (rule, node, kind of patch) so a block inserted ahead of an
id-less node does not lose it. One they still report but can no longer repair
(a plugin block now references the old `@id`) blocks the page rather than
being written around; after a later save it is retired and the others
proceed. A transient failure on a carried repair blocks the page for that run.

A staged repair whose page you save again by hand (WordPress drops the older
autosave) with the defect still there comes back as `pending` on the next
`audit --propose`; one whose defect is gone after such a save is marked
absorbed there too. `reject` withdraws a staged repair you looked at and do
not want: it leaves the carried set and is never written again.

Nothing is published, deleted, or written to post meta. Search Console access is
read-only: the token is requested with, and verified to carry, exactly the
`webmasters.readonly` scope.

**Credentials never leave the Keychain.** Every secret is read from the macOS
login Keychain at run time. Nothing secret is stored in this package, cached to
disk, printed, or placed on a command line where `ps` could see it. (Fetched
public page HTML and the approval ledger are the only things written to disk,
under `~/.cleanuprtx/`, mode `0600`.)

## Setup — first evening

Nothing to install. Standard library only, Python 3.9+, from the checkout:

```sh
cd cleanuprtx
python3 -m cleanuprtx doctor
```

`doctor` lists which Keychain items are missing and the command that creates
each one. Three credential flows:

```sh
# 1. WordPress: an application password for each site, minted on that site
#    (WP admin > Users > Profile > Application Passwords > name it "cleanuprtx").
#    The login used per site is shown by doctor and set in config.py (wp_account).
python3 -m cleanuprtx auth wordpress --site inspector-roofing
python3 -m cleanuprtx auth wordpress --site positive-outcomes
python3 -m cleanuprtx auth wordpress --site pnagolfcarts

# 2. Google: a Desktop-app OAuth client
#    console.cloud.google.com > APIs & Services > Library > enable "Google Search Console API"
#    > OAuth consent screen > External > add your own Google account under Test users
#    > Credentials > Create credentials > OAuth client ID > Desktop app
python3 -m cleanuprtx auth google-client      # prompts for the client ID and secret

# 3. Google: sign in once; a browser opens, you allow read-only access.
#    Sign in with the account that owns the Search Console properties - the same
#    one you added as a test user. Any other account is refused by Google.
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

If the browser tab says *access blocked* or *app not verified*, the account you
signed in with is not on the consent screen's test-user list. Add it and retry.

### If Google says `invalid_grant` later

While the OAuth consent screen is in *Testing*, Google expires refresh tokens
after seven days. Run `auth google` again, or publish the consent screen
(Google Cloud > OAuth consent screen > Publish app) to make tokens long-lived.

## Use

```sh
# Audit the whole site the way Google sees it. Read-only.
# ~1,100 live pages at --delay 0.25 takes 15-40 minutes; progress prints as it goes.
# Ctrl-C during the fetch keeps what was fetched and reports on it. Every fetched
# page is appended to ~/.cleanuprtx/cache/<site>.jsonl; re-run with --resume to
# reuse pages fetched in the last 24 hours (failed fetches are always retried).
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

**What to expect on inspector-roofing.com.** The site runs Breakdance and Rank
Math, so most JSON-LD Google sees is plugin-generated and the audit will report
it for hand-editing rather than staging drafts — `hand-edits` is the working
list, grouped by the plugin screen to open. Repairs are only staged for schema
that lives in a page's own `post_content` (a pasted `<script>` block). Drafts
are not public, so their schema is audited only with `--include-drafts`, which
reads each draft's `post_content` (one request per draft).

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

`indexing --from-search-console` inspects the URLs with the most impressions in
the last 90 days, 200 by default (`--max`). URL Inspection is limited to 2,000
requests per property per day; the tool stops the moment Google answers 429.

## Rules

| Rule | Severity | Google's wording | Repair |
|---|---|---|---|
| `profile-parent-node` | critical | *Invalid object type for field "\<parent_node\>"* | Keeps the identity the author named: a name gets a `@type`, a canonical name/URL becomes the canonical reference. Only an *absent* `mainEntity` is filled with the canonical Person, and only where that node exists on the page or on the canonical profile page itself. A wrong `@type` or a stranger's dangling `@id` is report only |
| `object-field-type` | warning | *Invalid object type for field "creator"* | A name → `{"@type": "Person"/"Organization", "name"}`; a URL matching a node on the page → `{"@id"}`; an unknown URL or unresolvable `@id` → report only |
| `invalid-datetime` | warning | *Invalid datetime value for "dateModified"* | On the node that stands for the page: `dateModified` ← the page's `modified_gmt`, `datePublished` ← `date_gmt`, both with explicit UTC offset. A nested Review, Comment or video keeps its own date: report only |
| `entity-fragmentation` | warning | — (splits the knowledge graph) | Rename the node's `@id` and every reference to it, across every block in `post_content`. Fires only for nodes that name or link to the canonical person/organization; other people are left alone. Report-only when a plugin block references the old `@id`, since renaming would leave that reference dangling |
| `invalid-jsonld` | critical | — | Report only |
| `stale-draft` | notice | — | Report only. On Breakdance sites judged by title and edit history, not body length |

`object-field-type` checks that the value is a Person/Organization object or a
reference that resolves to one *on the same page*. `mainEntity` is never checked
here: its schema.org range is Thing (FAQPage → Question, WebPage → Article) and
Google only requires a Person/Organization on a `ProfilePage`, which
`profile-parent-node` handles.

Canonical identities live in `config.py` per site. Only inspector-roofing has
them; the other two sites are never asked to merge anyone. "Organization"
means any schema.org Organization subtype (`RoofingContractor`,
`GeneralContractor`, `Store`, ...), never a fixed short list.

## Ledger

Approvals live in `~/.cleanuprtx/approvals.json` (`0600`, directory `0700`,
written atomically; every writer takes a lock and re-reads the file first).
Repair IDs are hashed over site, type, page, rule, the node's identity (its
`@id`, or its block in `post_content` plus path) and the *kind* of patch — never
the value it writes, which a page save or a sibling repair can change. So
re-running an audit never renumbers a decision, two identical defects on
different nodes never collide, and a rejection is final. If a re-audit changes
what an approved repair would write, it goes back to pending for re-approval.
Rows for pages the audit read but no longer reports are retired as `stale`,
and any stale row comes back as `pending` if the defect reappears; a repair
already fixed by hand is marked applied rather than retried forever. Rename
rows recorded by v0.2.2 (hashed by the old `@id` alone) are re-keyed on load.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

307 tests, no network and no Keychain. They include every write-path guard (the
body is only ever `content` plus the echoed `title`/`excerpt` on autosaves;
published pages go to `/autosaves`; media and Breakdance templates are refused; a
block that is not in `post_content`, or only inside an HTML comment, is
refused; two repairs on one page fold into one write), the three WordPress auth
diagnoses, Search Console response parsing and exact-scope enforcement, the
OAuth loopback handler, Keychain command construction, and a regression check
that this repository's own JSON-LD stays clean. Four of the files
(`test_adv_*.py`) were written by adversarial reviewers against the promises in
this README rather than against the code.

## Scope

cleanuprtx does not publish, does not delete, does not change any page's status,
does not write post meta or plugin settings, does not edit Breakdance canvases,
and does not submit anything to Google. Those stay manual on purpose.
