# Publishing Notes

## PyPI

1. Update `pyproject.toml` with real homepage, repository, author email, and version.
2. Build locally:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

3. Upload to TestPyPI first if desired.
4. Upload to PyPI with an account/API token:

```bash
python -m twine upload dist/*
```

## npm

1. Update `package.json` with real homepage, repository, and organization details.
2. Test the CLI locally:

```bash
node ./bin/roof-file.js init ./tmp-roof-file
node ./bin/roof-file.js score ./tmp-roof-file/inspection.json
```

3. Publish as public:

```bash
npm publish --access public
```

For a brand-owned namespace, consider publishing as `@inspector-roofing/protocols`.
