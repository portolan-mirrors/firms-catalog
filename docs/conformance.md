# Portolan Conformance

Conformance means passing [rashid](https://github.com/portolan-sdi/rashid),
not claiming to conform, so it runs in CI:

```bash
python3 tests/test_conformance.py
```

That gate fails on any error-severity finding whose rule is not listed below.
The list starts empty and it must never grow without a row here. A known
deviation with an issue number is a debt someone can pay off. A silently
widened allow-list is a false claim about what this catalog conforms to.

## The rashid version floor

The gate needs rashid `>=0.4.0,<0.5.0`. It reads `rashid --version` and fails
outside that range. It also fails when rashid is absent, and prints the install
command. A skip would report a green run for a catalog that no validator read.

The floor is 0.4.0 because it is the first release that bundles the Portolan
v0.3.0 profile this catalog declares. An older rashid does not know the v0.3.0
schema URI, so the v0.3 rules never run against the catalog.

Four places name this range: the gate, the CI install step, the two data
workflows, and this page. They must agree. They drifted once -- CI installed
`>=0.1.5` while the gate demanded 0.1.8 -- which passed only because pip
resolves to the newest, and would have failed confusingly the day 0.1.8 was
yanked.

The upper bound stops an unreviewed 0.5 rule set from changing what this gate
means. Raise both bounds together when you move to 0.5, and read the new rules
first.

## File metadata coverage

Every asset carries `file:size`. `file:checksum` covers the assets whose bytes
are local when a generator runs: the styles, the thumbnail, the logo, and the
two collection tilesets after a scheduled refresh. The published year files
and their aggregates gain a checksum when a consolidation stages that year's
bytes; hashing the published copies again would download tens of gigabytes
for values the next consolidation would replace. Until then those assets
carry `file:size` alone, which rashid reports as PTL-AST-003 warnings.

This file also records workarounds for the other validator CI runs. Those are
not conformance debts, because the catalog is correct and the validator is not.
They live here so nobody has to read CI code to find out why a gate skips
something.

## Accepted deviations

None.

<!--
When you accept one, add a row and a section explaining it, like this:

| Rule | Where | Why accepted | Tracking |
|---|---|---|---|
| PTL-VIZ-001 | all thumbnails | WebP is not yet permitted; the size saving is 4x | portolan-spec#121 |

Then add the rule id to ACCEPTED in tests/test_conformance.py. Both, or
neither.
-->

## Validator workarounds

### stac-check reports a dialect crash on every collection

`tests/test_stac_valid.py` exempts one stac-check failure:

```
'list' object has no attribute 'get'
[Schema: https://schemas.portolan-sdi.org/portolan/vX.Y.Z/schema.json]. Error in Extensions.
```

The Portolan schema is valid draft-07, and rashid validates catalogs against it
cleanly. `stac-validator`, which stac-check uses, hardcodes the JSON Schema
2020-12 dialect and ignores the `$schema` a schema declares. The profile schema
uses the draft-07 tuple form of `items` in `valid_bbox`, which means something
different under 2020-12, so the library raises instead of validating.

Tracked upstream at <https://github.com/stac-utils/stac-check/issues/159>,
and on the Portolan side at
<https://github.com/portolan-sdi/portolan-spec/issues/157>.

The exemption matches that exact message, and only when the failing schema is a
Portolan profile schema. Every other stac-check error still fails the build,
and the gate prints how many objects took the exemption.

The exemption expires on its own. The gate fails once stac-check stops emitting
the crash on a collection or item that declares the profile schema, and tells
you to delete both the exemption and this section. CI installs stac-check
unpinned, so the next release triggers that without anyone watching for it.

`tests/test_stac_valid.py` also fails when stac-check is absent, and prints the
install command. It takes no version floor and no pin. The rashid floor exists
because that gate asserts four named rules. This gate asserts no stac-check
rule. It needs the opposite property. A pin holds the exemption open after the
upstream fix ships.
