# Local Actions runners

Public local jobs request only `[factory-zero-point-modulator]`; these runners have no
GitHub default labels. Trusted non-PR jobs and same-repository PR-family jobs
whose author association is `OWNER`, `MEMBER` or `COLLABORATOR` use that label.
Forks, other author associations and missing PR/head metadata use the original
GitHub-hosted Ubuntu label instead. Routing changes only `runs-on`, not existing
job conditions, build commands, qualification gates or artifact actions.
Artifact storage/billing is separate from runner compute.

Complete hosted-byte exceptions are `managed-publication-execution.yml`,
`native-alpine-candidate.yml` and `oci-destination-observation.yml`. The first has
an exact deterministic permission/admission header contract; the latter two are
bound by frozen source plans. Every job and condition in each exception stays at
its approved original bytes, not just native/observation jobs. Ordinary source
`ci.yml` remains locally routed. Restoring these workflows neither refreshes any
source assertion/plan/receipt nor admits native work; the consumed historical OCI
plan has pre-existing source drift and is not claimed to pass at current HEAD.

One runner executes one job at a time for this repository (including matrix
jobs). Its per-repository runner/daemon envelope is 2 CPU and 4 GiB RAM.
All repositories share a host-wide aggregate ceiling of 3 CPU and 10 GiB RAM;
per-repository limits are ceilings, not simultaneous capacity guarantees.
An offline host leaves jobs queued until its matching runner returns; GitHub's
normal queue expiry still applies.

External-contributor jobs are not skipped by routing. Normal GitHub approval
requirements still apply. Keep branch creation/write access restricted to trusted
collaborators; PR-controlled workflow YAML is not an immutable isolation boundary.
A maintainer must review workflow changes before permitting untrusted execution.

The default branch also has a manual, owner-only `local-runner-health.yml` smoke
workflow using the custom label. It is separate from normal contributor CI and
does not qualify workloads or change retained evidence.

Docker workloads require a private per-repository Docker daemon and state;
never mount the host Docker socket. Qualification scripts may require a local
Unix socket, shared workspace paths, or their own rootful `dockerd`; do not
silently relax these requirements to accommodate a different runner topology.
