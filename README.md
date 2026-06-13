# Standards GitHub Pages Hotfix

This fixes `https://standards.inspector-roofing.com/` returning a GitHub Pages 404.

## What Was Wrong

The repository publishes GitHub Pages from the `docs/` folder. The live YAML exists at:

`docs/api/openapi.yaml`

But the custom domain file was only at the repository root:

`CNAME`

For a `docs/` Pages source, GitHub needs the custom domain file inside:

`docs/CNAME`

The `docs/` folder also did not have a homepage:

`docs/index.html`

So the standards domain root had no page to serve.

## Files To Upload

Upload these two files to the `main` branch of `RichNass87/inspector-roofing-protocols`:

- `docs/CNAME`
- `docs/index.html`

## After Upload

1. Wait 2-5 minutes for GitHub Pages to rebuild.
2. Open `https://standards.inspector-roofing.com/`.
3. Open `https://standards.inspector-roofing.com/api/openapi.yaml`.
4. If the root still shows 404, go to repository `Settings > Pages`.
5. Confirm the source is `Deploy from a branch`, branch `main`, folder `/docs`.
6. Confirm the custom domain is `standards.inspector-roofing.com`.
