## This Repository

A template for a git-backed Portolan catalog. Repositories created from it
inherit everything here, so a mistake in this file propagates.

### The publish boundary

`catalog/` is the published catalog. Everything in it is published, and
nothing outside it ever is. Do not move a file into `catalog/` to make it
publish, and do not add a path outside `publish_dir` to `tools/publish.py`.
The boundary is the only thing standing between a scratch file and a public
bucket, and it holds because it is structural rather than a list of
exclusions.

### Data never enters git

Never commit a GeoParquet, COG, PMTiles, Zarr, or COPC file. If a gate needs
bytes to check, generate them in CI. Git keeps every version of a binary
forever and deleting it later reclaims nothing.

### The conformance allow-list

`ACCEPTED` in `tests/test_conformance.py` ships empty. Never add an entry
without a matching row in `docs/conformance.md` giving the rule, where it
fires, why it is accepted, and the issue tracking its removal.

### Published agent guides

Every claim in a `catalog/**/AGENTS.md` is either quoted from a source or
measured from the data. An invented join key or column name produces a
confident wrong answer that nothing downstream catches.

### The links back to this repository

`catalog/catalog.json` ships a `vcs` link and an `issues` link. Both hrefs are
setup placeholders, so `tests/test_setup.py` catches a half-edited repository.
The Portolan spec recommends these two links for a git-backed catalog. See
[git-backed catalogs](https://github.com/portolan-sdi/portolan-spec/blob/main/specs/best-practices/git-backed-catalogs.md),
which merged in August 2026.

Keep both hrefs absolute. The repository sits outside the published catalog,
so a relative href resolves against the public base URL. Each placeholder
holds a `://`, because `tests/test_links.py` treats an href without one as a
path and looks for it on disk.
