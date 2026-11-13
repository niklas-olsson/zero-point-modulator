# Managed publication qualification

The manually dispatched Managed publication qualification workflow produces one
actual draft from a real managed root and two children against this repository's
exact approved main revision. Its model replies are deterministic fixtures.
The workflow qualifies execution, composition and forge delivery, not model
reasoning or live-provider compatibility.

## Before dispatch

Merge and review the workflow infrastructure first. Dispatch only its reviewed
default-branch revision. Confirm that the repository permits GitHub Actions to
create pull requests. The job requests contents and pull-requests write access.
The approved base must remain current through publication; a changed base is a
failure to reconcile, not permission to rebase an existing candidate.

The workflow has two separate steps. Execution has no forge-token environment.
It runs one parent and two children, completes the full repository unit suite in
the confined root, captures canonical results and stops every owned service and
the model fixture. A new publisher-only process then reopens the original
authority with an environment that excludes the forge credential. It invokes
the installed publisher twice against the same private journal and candidate.

## Operator configuration

The example at ../examples/managed-publication.json is a template, not an
executable deployment configuration. Replace its source, state, approved base,
deployment and authority endpoint with the original installation's values.
Keep distinct explicit host references for authority and forge credentials.
Never copy credential values into this file, Git URLs, command arguments,
workspace files or reports. The qualification workflow constructs its privileged
configuration from its sealed installation and fixed policy; generated task
files cannot choose those values.

The publisher must observe the actual repository ID, approved base, composite
commit/tree, provenance marker and one open draft. A second fresh publisher
process must return that same proposal. Existing-ready, closed, stale, uncertain
or unavailable results do not pass this new-draft trial.

## Review and recovery

The resulting draft is a normal review vehicle. Approve its GitHub Actions
workflows when GitHub requests writer approval, run the repository's complete
checks, and request an eligible independent reviewer against the actual head and
base. Publication grants no acceptance, readiness or merge authority.

An ambiguous result stops the trial. Do not rerun execution to manufacture a
replacement candidate or proposal. Inspect the original operation and actual
forge state. A workflow rerun has a new runner and cannot resume the original
private journal; automatic reruns are refused. A later manual dispatch requires
a separate decision after the original operation is reconciled.

Only safe aggregate IDs, observed commit/tree/PR identity and hashes are retained
as workflow evidence. Private databases, credentials, captures and publication
journals are not uploaded. Fresh-process replay is tested within one job.
Cross-job recovery and recovery after loss of the runner are not qualified.

The root's command guard is 120 seconds and the inner suite guard is 110 seconds.
Each child retains its original 90-second deadline until the whole-worker fence,
so the earliest child deadline can end the root sooner. Qualification must finish
within that effective bound. The report records the discovered and executed test
counts and actual suite elapsed time. A timeout fails without extending grants.
The complete suite log remains only in the private captured workspace; exported
evidence contains its hash and a bounded verification summary.
The suite uses a fresh private temporary directory in the owned root workspace,
outside the candidate checkout, so installation fixtures can execute their copied
interpreters while /tmp remains noexec. Success removes the empty directory;
failure retains it privately and never reuses it.

A passing draft does not prove the separate combined-test-failure scenario.
