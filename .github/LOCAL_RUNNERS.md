# Local Actions runners

Public local jobs request only `[factory-zero-point-modulator]`; these runners have no
GitHub default labels. Trusted non-PR jobs and same-repository PR-family jobs
whose author association is `OWNER`, `MEMBER` or `COLLABORATOR` use that label.
Forks, other author associations and missing PR/head metadata use the original
GitHub-hosted Ubuntu label instead. Routing changes only `runs-on`, not existing
job conditions, build commands, qualification gates or artifact actions.
Artifact storage/billing is separate from runner compute.

One on-demand pool supplies one runner/job at a time for this repository
(including matrix jobs). Its per-repository runner/daemon envelope is 2 CPU and 4 GiB RAM.
All repositories share a host-wide aggregate ceiling of 3 CPU and 10 GiB RAM;
per-repository limits are ceilings, not simultaneous capacity guarantees.
An offline host leaves jobs queued until its matching runner returns; GitHub's
normal queue expiry still applies.

External-contributor jobs are not skipped by routing. Normal GitHub approval
requirements still apply. Keep branch creation/write access restricted to trusted
collaborators; PR-controlled workflow YAML is not an immutable isolation boundary.
A maintainer must review workflow changes before permitting untrusted execution.

Docker workloads require a private per-repository Docker daemon and state;
never mount the host Docker socket. Qualification scripts may require a local
Unix socket, shared workspace paths, or their own rootful `dockerd`; do not
silently relax these requirements to accommodate a different runner topology.

## Frozen hosted exceptions

`managed-publication-execution.yml` remains entirely byte-identical to original
HEAD on its original hosted runner: the mandatory deterministic
`test_publication_execution_workflow.py` suite binds its complete manual header,
including the hosted runner, permission and admission boundary. Routing alone is
not authority to change that contract; its assertions and execution steps stay
unchanged. Normal `ci.yml` source validation remains locally routed for trusted
jobs with the original hosted fallback for other contributors.

`native-alpine-candidate.yml` stays entirely unchanged on its original hosted
Ubuntu labels: mandatory application regression tests validate its exact bytes
against `active-plan-13.json` (`test_alpine_runtime_checkpoint.py`).
`oci-destination-observation.yml` also stays hosted and byte-identical because
`observation-plan-1.json` binds its exact source bytes. No plan hash, sealed
receipt, assertion or historical evidence is refreshed or waived.

Native trials retain exact Docker engine/socket, interpreter, pidfd, storage
and available-memory prerequisites. A private Docker daemon with matching
workspace paths is necessary but does not itself qualify this local host.
