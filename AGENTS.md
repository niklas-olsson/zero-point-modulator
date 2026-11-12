# Repository instructions

ZPM owns repository enrollment, top-level worker capacity and installation.
OpenJules owns execution, confinement, children, capture and publication.
Keystone Polyphony owns collaboration and canonical protected-task authority.
Keep work pull-based, preserve independent agency, and retain durable state when
capacity reaches zero.

Start with the [README contributor map](README.md#contributor-map) to find source
and canonical documentation. Update that explanation when behavior, commands,
configuration, recovery, guarantees or evidence change. Update the map when
routing changes. Internal refactoring needs no cosmetic documentation or routine
"no docs" declaration. Preserve historical evidence, attribution and uncertainty.

Keep changes focused. Preserve stable launch/request keys, uncertain-outcome
reservations, scoped ownership and capture-before-release recovery. Follow the
existing [CI workflow](.github/workflows/ci.yml): `python -m unittest discover -v`,
package installation and CLI smoke checks. Changed integration contracts require
the relevant installed trials against explicit ZPM, OpenJules and Polyphony
revisions; update pins and their trial documentation together.

Distinguish deterministic fixtures, actual installed processes, live-provider
behavior and forge outcomes. Record demonstrated criteria and remaining work in
the existing issues, using [#3](https://github.com/niklas-olsson/zero-point-modulator/issues/3)
for integrated acceptance. Separate implementation from review and retain
unresolved findings. Follow existing maintainer authorization and repository
policy for publication and merging.
