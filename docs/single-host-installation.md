# Install the managed single-host baseline

ZPM owns installation configuration and repository capacity. Polyphony owns
task requirements, attempts, budgets and artifact provenance. OpenJules owns
the Docker execution boundary and runtime receipts. The installation does not
introduce another task scheduler or use a browser VM as a production worker.

`zpm deployment prepare` creates a private installation directory from explicit
operator choices. It performs local validation and writes configuration. It
does not install packages, contact a provider, create Docker containers or prove
that the services are ready. `verify` is also an offline integrity check.

## Supported host and execution boundary

- Linux with Python 3.11 or newer, Git and a local Docker Engine.
- New preparation requires libc and the local filesystem to support Linux
  `renameat2(RENAME_NOREPLACE)`, plus directory `fsync`. Unsupported atomic
  publication fails closed. The destination's existing parent directory must
  be trusted by the operator.
- The three application checkouts and every enrolled source checkout must be clean
  and fixed at the full Git revisions declared in configuration.
- Keep all databases and lock files on local storage. A copied SQLite database
  is not a second active deployment or a cross-host failover mechanism.
- The trusted OpenJules service account must be authorized to use the host's
  Docker Engine. Docker socket access is host-control authority. The installation
  never mounts that socket into the task container and does not start an
  unauthenticated Docker-in-Docker TCP service.
- The approved task image must already exist locally and be identified by its
  full image ID or repository digest. Preparation and startup do not implicitly
  pull a mutable image tag.
- A managed profile runs one root attempt per worker incarnation, with no
  container networking. An optional explicit model policy enables host-brokered
  inference over the worker's private Unix socket. The model path requires
  rootful Docker with the approved task UID mapping; rootless Docker and user
  namespace remapping need separate qualification.
- Root execution is the default. An explicit `child_policy` enables bounded
  children only with matching qualified OpenJules and Polyphony revisions and
  a new approved runtime profile. Independent semantic review and forge
  publication have separate interfaces. Deterministic
  model fixture results do not establish a live provider's compatibility or
  model quality.

Polyphony and OpenJules both use Python packages named `src`. The generated
manager installs each service in a separate virtual environment and starts each
from its own pinned checkout. Do not combine their source directories into one
`PYTHONPATH`.

Configuration schema 1 enrolls one repository with one approved profile.
Schema 2 prepares 2–100 repository entries within one deployment, sharing one
ZPM controller, one OpenJules API and one Polyphony authority. It keeps one
approved profile per repository. The generated component configurations retain
their existing schema version 1.

Two independent bundles can also share the same Docker Engine with distinct
deployment IDs, private directories and service ports. The
[installed isolation trial](managed-isolation-stack-trial.md) exercises that
schema-1 topology, including one installation's outage and removal while the
other task continues. Its result does not establish the new shared-service
topology. The separate [schema-2 trial](managed-multi-repository-stack-trial.md)
records shared-service qualification for its exact tested revision set.
Neither topology supports multiple deployments behind one OpenJules API.
Trusted services sharing Docker access are not mutually isolated host tenants.

## Prepare an installation

For one repository, start from [the schema-1 example](../examples/deployment.json).
For several repositories, use [the schema-2 example](../examples/deployment-multiple.json)
and the configuration contract below.
Replace every placeholder with your actual absolute checkout paths, exact
revisions, immutable image ID, approved command and acceptance policy. The
enrolled source repository's declared revision is also the immutable execution
base. A different base or command requires a separately approved profile and
configuration; it is not supplied by a task request or ZPM enrollment payload.

The repository binding uses the same schema as `zpm enroll`. The deployment
ceiling limits admission across reservations. It does not claim the selected
count has been provisioned. Beginning with `desired_workers: 0` lets the operator
inspect the installation before explicitly requesting capacity.

```sh
zpm deployment prepare /absolute/operator-deployment.json \
  --directory /absolute/private/zpm-installation
zpm deployment verify /absolute/private/zpm-installation
```

The destination must be outside all source checkouts. A new destination receives
the private layout below. Repeating preparation with the same normalized
configuration verifies and reuses a complete installation, returning
`reused: true`. It retains credentials, environments, artifacts and the current
enrollment state, including later target, pause or removal changes. It does not
start services or re-enable a removed repository.

Different configuration, a symlink, an incomplete destination or modified sealed
files are rejected. Verification reads the existing ZPM journal without creating
it and requires its original deployment identity and retained enrollment. A
missing or unreadable journal must be restored before restart. This is not a
version-upgrade operation. Keep a legacy incomplete destination for diagnosis;
use a new destination for a fresh preparation. No
existing resources are overwritten or deleted.

| Path | Purpose |
| --- | --- |
| `manifest.json` | Approved normalized choices, exact source revisions and configuration integrity hashes |
| `preparation.json` | Private preparation identity, final destination and normalized-configuration digest, sealed by the manifest |
| `config/polyphony.json` | Canonical repository, profile, base, attempt and acceptance policy |
| `config/openjules.json` | Approved Docker profile, lifecycle state, authority endpoint and secret references |
| `config/provider.json` | ZPM's scoped OpenJules provider configuration |
| `config/repository.json` | Schema-1 initial ZPM enrollment |
| `config/repository-<repository-sha256>.json` | Schema-2 initial enrollment for one canonical repository; the prepare report and generated instructions list each exact path |
| `secrets/` | Separate owner, reviewer, ingress, runtime and ZPM credentials |
| `state/` | Durable ZPM, authority and runtime journals and artifacts |
| `environments/` | Three isolated Python environments created by bootstrap |
| `manage.py` | Explicit bootstrap, foreground start, offline verify and online doctor commands |

The root and configuration directories use mode `0700`; secret files use mode
`0600`. No credential values appear in the manifest, normal configuration or
command arguments. Ingress and reviewer tokens are scoped to one repository.
The trusted controller and runtime credentials cover the deployment's entire
configured repository set so the shared services can perform their owned work.
The owner and reviewer credentials identify the same `operator` principal.
They do not create two independent reviewers. The runtime is a separate
principal and cannot use its credential to accept its own work.


## Prepare several repositories in one deployment

Schema 2 keeps deployment settings at the top level and places execution and
enrollment settings in its `repositories` list:

| Location | Fields |
| --- | --- |
| Shared deployment | `schema_version: 2`, `deployment_id`, `max_workers`, `sources`, optional `ports` |
| Each repository entry | `binding`, `source`, `image`, `command`, `policy` |
| Optional per-entry settings | `runtime`, `environment_refs`, `model_policy`, `child_policy` |

The binding remains schema 1 and uses the same canonical GitHub identity and
capacity fields as the existing CLI. Duplicate canonical identities and
unexpected fields fail before publication. A profile name can be reused in
different repositories because its full scope includes the repository. Each
source checkout and immutable revision remain explicit. An approved checkout
may also be reused, including a component checkout; the installation directory
must remain outside every source checkout. Isolation evidence must describe
the actual sources used in its trial.

Preparation normalizes and sorts entries by canonical repository identity.
Reordering equivalent input therefore reuses the same complete installation.
Shared application checkouts are verified once per configuration parse; each
repository's source and policy are still validated. The schema-1 normalized
shape, paths and environment aliases remain unchanged.

The 100-repository upper bound comes from the single Polyphony runtime
credential's scope-list limit. The exact serialized OpenJules configuration and
credential files must each fit 1,000,000 UTF-8 bytes, including formatting and the
final newline. Oversized output fails before atomic publication. These are
configuration/parser limits, not a claim that a 100-repository workload has been
qualified. Existing dispatcher concurrency and resource limits still apply.

The prepare report has a `repositories` list containing each canonical
`repository` and its relative `binding_file`. Those flat configuration files use
the full lowercase SHA-256 of the canonical identity in their filenames.
The generated `README.txt` repeats the mapping for use with `zpm enroll`.
There is one ZPM ownership journal and one authority journal for the deployment.

### Credentials and environment references

Schema-2 `secrets/operator.json` contains `schema_version: 2`, one `owner` token,
and a `repositories` object keyed by canonical repository identity. Each entry
contains its own `ingress` and `reviewer` tokens and a nonsecret
`request_key_prefix`. The prefix is operator metadata; the server does not
automatically apply or enforce it. Prefix ingress and review request keys with
that value, then append a stable unique operation identifier. A review retry is
identified from the reviewer identity and full request key, so its suffix must
distinguish the candidate, check and evidence observation. After an uncertain
response, retry the exact full key with the unchanged payload. Changed evidence
or a new operation requires a new key.

The generated prefix occupies 76 UTF-8 bytes. Managed ingress permits a total
request key of 512 UTF-8 bytes, leaving at most 436 for its suffix. Different
reviewer tokens still identify the same operator and do not establish independent
review.

The controller token intentionally authorizes every configured lifecycle scope;
the runtime token intentionally authorizes every configured canonical scope.
Using either trusted host credential with a correctly bound second repository
is authorized. Repository-specific ingress/reviewer credentials remain scoped,
and a worker, attempt or artifact must still match the scope in its request.

Each guest credential reference resolves through an alias derived from its
canonical repository and approved target:
`ZPM_REPOSITORY_<UPPERCASE_REPOSITORY_SHA256>_GUEST_<TARGET>`. The approved guest
target names, such as `OPENAI_API_KEY`, remain unchanged. Each host-brokered model
policy instead uses
`ZPM_REPOSITORY_<UPPERCASE_REPOSITORY_SHA256>_PROVIDER_TOKEN`. Aliases do not depend
on input order, secret values or the source environment variable's name.
Collisions fail closed. The operator's referenced values resolve only when
starting OpenJules; they are never stored in the manifest or generated config.
ZPM and Polyphony do not receive those values or unrelated host secrets.

The example uses separate `PROJECT_A_OPENAI_KEY` and `PROJECT_B_OPENAI_KEY`
references. Supply them through the host's secret mechanism before starting the
manager. A model-backed repository still rejects guest credential injection and
keeps its provider token in the trusted broker. Model and child policies remain
bound to the exact repository/profile entry.

### Capacity, removal and qualification

Use the same installed CLI and shared state file for each repository:

```sh
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project-a 1
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project-b 1
environments/zpm/bin/zpm --state state/zpm.sqlite3 status
python3 manage.py doctor
environments/zpm/bin/zpm --state state/zpm.sqlite3 pause owner/project-a
environments/zpm/bin/zpm --state state/zpm.sqlite3 remove owner/project-a
```

Local `status` and `plan` read retained journal observations without loading the
provider configuration. Their deployment `max_workers` is therefore `null`, and
they do not compute the configured deployment-ceiling reason. The generated
`doctor` command supplies the provider credentials and configuration, performs
authenticated scoped capability checks, and includes that ceiling with the
journalled capacity report. Its `ready` flag describes capability validation;
it does not prove that every desired worker target has converged.

The deployment ceiling counts all unreleased reservations across repositories,
including uncertain or draining workers. Per-repository desired targets remain
visible when a ceiling prevents admission. Existing rotating mutation credits
remain in place; this change does not introduce a scheduler or promise a
wall-clock fairness bound. Children do not add top-level reservations.

Pause and removal change capacity admission while keeping canonical policy,
credentials, task history and artifacts. Repeated preparation checks that every
original enrollment row and the deployment identity remain present; it never
recreates a missing journal or resets current targets, pause or removed state.
Explicitly re-enroll a removed repository using its reported binding file.
Schema-2 configuration is for fresh installations. Adding a repository, changing
the repository set or approved pins/profile, or converting a schema-1 bundle
remains an unsupported upgrade and cannot overwrite an existing bundle.

The generator tests cover configuration, scoped credentials, exact output
size, secret resolution and retained state. The separate
[shared-service installed trial](managed-multi-repository-stack-trial.md) exercises
two schema-2 repositories with one controller and one service pair. Its dedicated
Docker job must pass on the intended revision set before claiming this topology;
the other installed trials exercise schema 1. Its
[retained qualification](managed-multi-repository-stack-trial.md#retained-qualification)
records the first passing eight-job revision set, including both canonical
captures after restart. Refreshing a component pin requires a new complete run.
Its source-failure evidence must stay scoped: a shared service, authority or
Docker outage affects both repositories, and broker readiness can couple model
profiles when an endpoint is unavailable. An already-running B task may continue
while A's source is unavailable. However, verify, bootstrap, start and doctor
revalidate every approved checkout; verifying or restarting the whole bundle
fails closed until A's source and exact pin are restored. Source-failure evidence
must not imply cold restart or general service isolation during that fault.
Live provider behavior and actual
forge review/publication remain separate acceptance gates in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).

## Retry interrupted preparation

New preparation writes a private `.zpm-prepare-*` sibling directory. Its marker
records the intended final destination, and generated state and credential paths
already name that final location. The generated manager rejects the unpublished stage
as a moved bundle before starting or bootstrapping any service. Stage files and
directory entries are flushed before
[atomic no-replace publication](https://man7.org/linux/man-pages/man2/rename.2.html).
The parent directory is then
[flushed explicitly](https://man7.org/linux/man-pages/man2/fsync.2.html).

If preparation is interrupted, rerun the same command with the same configuration
and destination. If the final directory is absent, the retry creates a fresh
private stage. It never edits, resumes or deletes an abandoned stage. If the
complete bundle was already published, retry verifies it and reuses its existing
credentials, deployment identity and current enrollment state. A destination
created by another writer is preserved, including an empty directory or symlink.
Cooperating preparers share a parent-directory lock; that advisory lock does not
make a hostile parent directory trustworthy.

Abandoned stages contain private credentials and remain available for diagnosis.
Their markers are evidence of the preparation attempt, not authorization to
repair an arbitrary destination. Legacy or foreign incomplete final directories
still fail closed. The
[recovery tests](../tests/test_preparation_recovery.py) use real `SIGKILL` at the
credential-write and publication boundaries, concurrent preparers and foreign
destination races. They verify process interruption, not hardware power-loss
behavior or a version upgrade.

Do not publish, commit or copy the generated directory to a public location.
The input configuration contains secret references only. Keep actual model
credentials in the host's secret mechanism. The manager does not inherit
unrelated environment credentials or `PYTHONPATH` into the services. Existing
command-only profiles retain their explicit `environment_refs` behavior.
Model-backed profiles reject guest environment references and keep the provider
credential exclusively in the trusted OpenJules process.

## Enable a model-backed root agent

Start from [the model deployment example](../examples/deployment-model.json).
Choose and review the model endpoint, model identifier, limits and credential
reference explicitly. The optional `model_policy` is at the top level in schema 1
and inside the repository entry in schema 2. It is bound to the same deployment,
canonical repository and profile as the approved Docker configuration. Omitting
it keeps that repository's command-only path and does not choose a provider.

The image must contain OpenJules' managed runner. Build the approved OpenJules
revision's `docker/Dockerfile.managed-agent` with an explicitly chosen Python
base digest, install any repository test dependencies into that image, and
approve its resulting immutable image ID. Task execution cannot download
dependencies. The example command invokes the runner with its fixed socket
path, a turn limit and operator-selected verification argv. Repository task
input cannot replace the image, command or model policy.

| Model policy field | Meaning and accepted bounds |
| --- | --- |
| `endpoint` | Complete HTTPS URL ending in `/chat/completions`; loopback HTTP is allowed for a local provider or fixture. URL credentials, queries and fragments are rejected. |
| `model` | Explicit nonempty model identifier, at most 256 characters, without whitespace |
| `token_ref` | An uppercase `env:NAME` reference supplied to the start command by the host secret mechanism; control-token references are rejected |
| `max_calls` | Reserved calls per attempt, 1–1000; default 25 |
| `max_output_tokens` | Requested output-token allowance per call, 1–32768; default 2048 |
| `max_total_output_tokens` | Total reserved output-token allowance per attempt, 1–10000000; default 32768 |
| `request_timeout_seconds` | Positive timeout up to 120 seconds, also constrained by canonical permission; default 30 |
| `max_request_bytes` | Bounded broker request size, 1024–524288; default 524288 |
| `max_response_bytes` | Bounded provider response size, 1024–2097152; default 2097152 |
| `token_parameter` | `max_tokens` or `max_completion_tokens`, according to the selected provider; default `max_tokens` |

The generated OpenJules configuration contains a scoped `models` entry whose
credential reference is `env:ZPM_MODEL_PROVIDER_TOKEN`. The manager resolves the
operator's source reference into that internal alias only for OpenJules. The
actual value is absent from generated files, command argv, ZPM, Polyphony,
bootstrap processes and the worker environment. The container receives only
its own read-only model directory with a Unix socket. It keeps disabled
networking and receives neither the Docker socket nor control credentials.

Supply the named provider reference through the host's secret mechanism before
running `python3 manage.py start`. Preparation, offline verification and
dependency bootstrap do not require that secret. Startup and model readiness
fail closed when it is missing. Doctor checks the scoped dispatch and local
model transport without making an inference call; a ready result does not prove
that the remote provider accepts the selected API or model.

Changing the endpoint, model, limits or runtime UID requires a new approved
profile name and configuration. Provider credential value rotation can retain
its existing reference. Preserve the model journal across restarts: completed
identical requests are cached, while uncertain requests retain their reserved
budget and are not automatically sent again. Canonical permission is checked
before inference and before delivering output.

The limits bound calls and reserved output tokens per attempt. They do not
guarantee a monetary ceiling: input tokens, provider-specific reasoning behavior
and already dispatched requests have separate billing implications. Multiple
workers and deliberate new attempts have distinct budgets.

## Enable bounded children inside a worker

Start from [the child deployment example](../examples/deployment-children.json)
and select component revisions that pass the child installation trial. The
optional `child_policy` requires `model_policy` and the child entrypoints in
both OpenJules and Polyphony. It creates matching scoped runtime policy and
canonical grant limits. It adds no services, task ledger or ZPM replicas.

Use a new approved profile name and a managed-agent image containing the root
wrapper and child tools. The root command invokes `managed_root_runner.py`
with separate model and child-control socket paths. The approved child command
uses the ordinary `managed_runner.py`; the host appends its exact model socket,
turn bound and optional verification argv. Repository instructions never choose
the command, UID, provider, credentials or execution identity.

| Child policy field | Meaning and accepted bounds |
| --- | --- |
| `max_children` | Lifetime children within one root attempt, 1–64; completion does not refund a slot |
| `max_child_seconds` | Positive duration, at most the approved root attempt duration and 86,400 seconds |
| `max_child_turns` | Canonical inference turns per child, 1–1,000 |
| `capabilities` | Exact approved runner capability bundle, contained in the root's declared capabilities |
| `uid_start` | First distinct child UID, default 60,000; the complete range must exclude root guest UID, watchdog UID 65,534 and zero |
| `command` | Operator-approved child runner argv; defaults to the ordinary managed runner |
| `verify_argv` | Optional operator-approved child verification argv; defaults to no extra command |
| `max_result_bytes` | Bound per child patch/log result, 1,024–4,000,000 bytes; default 4,000,000 |
| `outcome_protocol` | Optional `initial-v1`, only with the default managed child runner; requires canonical `child_initial_outcome: 1` support |

These capability names describe the approved runner bundle. Removing a model
tool does not establish a new operating-system privilege boundary. Each child
uses its own private workspace and UID inside the existing worker container,
starting from the same approved immutable source base. Children share the
root's model call/output-token allowance and reserve additional time against
the same canonical task budget. Set that budget for the root plus the child
allocations you intend to permit; a maximum child count does not promise that
every requested allocation will fit.

**Expiry and cancellation retire the whole worker in this first version.** An
exited child command does not prove all its descendants stopped. Child patch
and log reads remain provisional until the host stops the owning container and
captures each child before finalizing the root. The earliest child deadline or
an explicit child cancellation therefore stops that root and its siblings.
Other workers and independent peers are unaffected. Admission closes on drain;
unknown termination or failed capture retains the worker reservation and work.

The root may combine provisional outputs, but later publication must verify
the final canonical artifact hashes and the actual composite commit. Neither
an agent's summary nor a runtime capture supplies independent acceptance.
Changing child policy requires a new approved profile and worker incarnation;
an existing root-only watchdog cannot acquire children through a config edit.
Omitting `child_policy` preserves the existing root-only profile and behavior.

With `child_policy.outcome_protocol: "initial-v1"`, a child explicitly reports
`completed`, `declined`, `needs_clarification` or `failed` through its own
authenticated host callback. The root can inspect the provisional report with
`child_status`. The final canonical child record retains that report and its
exact captured artifacts for authorized retrieval after service restart.
Completion remains a self-report, separate from verification, termination and
independent acceptance. Decline and clarification preserve partial work and do
not compel another child execution. A child that never reports stays unreported.

The setting is omitted from legacy child policy serialization. Enabling it
changes the runtime policy fingerprint and requires a new approved profile and
worker incarnation. A missing authority capability blocks readiness, even if
the authority responds successfully. This first outcome refers to the initial
instruction only. It does not add a second live instruction, public conversation
or automatic replay of a parent agent process.

The [child installation trial](managed-children-stack-trial.md) records the
candidate component pins and required qualification gates. It exercises graceful
target-zero draining while the root and both children remain active. It retains the worker reservation until
final canonical captures exist, then verifies restart without new inference.

## Clean a captured worker's runtime resources

The pinned OpenJules runtime provides an
[explicit host cleanup command](https://github.com/niklas-olsson/OpenJules/blob/3a77641e56055daa5c3a2b8b2a3c337ef9be5a2e/docs/managed-docker-runtime.md#explicit-host-cleanup).
Use its installed environment, generated `config/openjules.json`, and original
deployment/repository/worker scope. Stop the OpenJules service through its
supervisor first and retain access to the original canonical authority. The
generated foreground manager stops services together; the
[child trial's cleanup phase](managed-children-stack-trial.md#explicit-cleanup-boundary)
reopens that same installed authority on its original private database and port
while cleanup runs, then resumes the normal full-service restart.

Only proven executed captures qualify. Idle, legacy or ambiguous resource
creation remains retained. Host archives, journals and canonical artifacts stay
available, and managed resource names must never be manually recycled. Removal
and target zero continue to preserve runtime resources unless this separate
OpenJules operation is explicitly invoked.

## Publish a final child composition

The pinned OpenJules child baseline also includes an explicit operator command
for [final artifact composition and draft PR publication](https://github.com/niklas-olsson/OpenJules/blob/3a77641e56055daa5c3a2b8b2a3c337ef9be5a2e/docs/managed-publication.md).
Run it from the approved OpenJules checkout with that service's environment.
Its separate private configuration selects the captured canonical candidate,
exact source base, numeric GitHub repository identity, target branch, feature
namespace, commit identity and explicit host credential references. Publication
does not start another service or add worker capacity.

The command independently composes every final child patch and requires its Git
tree to match the canonical root candidate. It creates a normal draft only;
repository CI and independent review must cover the actual head and base before
promotion or merge. The generated manager does not publish automatically, and
the installation trial uses no live forge credential. Completing that live
workflow remains a separate qualification step.

## Install dependencies and start

From the generated directory:

```sh
python3 manage.py bootstrap
python3 manage.py start
```

Bootstrap is the explicit dependency-installing step. It uses each repository's
declared dependency file and records the resolved versions in that service's
`environments/<service>/resolved-requirements.txt`. Application revisions are
pinned. Existing upstream dependency ranges are not represented as a complete
reproducible dependency lockfile. Re-running bootstrap retains completed
environments instead of silently upgrading them.

`start` runs all three services in the foreground. One local manager lock avoids
two managers operating the same directory. The listeners bind to `127.0.0.1`,
on separate configured ports. ZPM uses its existing `reconcile --watch` loop and
controller lock. A browser, reverse proxy, public ingress or external hosting
account is not required.

If a service exits, the manager stops its remaining service processes. `Ctrl-C`
or `SIGTERM` also stops the services. This process management does not assert
that a task has terminated or that capture succeeded. Worker containers,
runtime tombstones, journals and artifacts remain available for recovery.

## Check actual capability and request capacity

In a second terminal, from the installation directory:

```sh
python3 manage.py doctor
```

Doctor runs the actual ZPM to OpenJules scoped capability check. It returns a
nonzero exit status when managed dispatch, profile support or repository access
is unavailable. It is not a coding-task, child-agent, review or merge test. No
passing `verify` output substitutes for this check.

Use the installed ZPM CLI against the generated state file to change desired
capacity. The already running watch process reconciles the new target:

```sh
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project 2
environments/zpm/bin/zpm --state state/zpm.sqlite3 status
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project 3
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project 1
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project 0
```

Status reads journalled observations. It distinguishes healthy workers from
reserved, draining and uncertain capacity. A target edit alone is not a receipt
that the requested transition completed. Confirm that reservations reach zero
before treating scale-down as complete.

The service's managed ingress uses the dedicated ingress credential in
`secrets/operator.json`. For schema 2, select the token under the matching canonical
repository and use its request-key prefix. Submit public task requirements with
a stable source and explicit execution-request key. Repeating
the same request returns the same canonical task; changed retries conflict.
Task budgets and acceptance policy come from the installed authority policy.

## Recovery and evidence

Restart with the same installation directory, source revisions and local state.
Changing source files, moving the directory, changing file permissions or
editing sealed configuration causes offline verification to fail. Configuration
verification is for accidental drift detection; someone who can rewrite the
private manifest already has the deployment operator's authority.

Preserve the authority database and runtime captures independently of worker
capacity. Do not delete retained Docker containers or their name tombstones
while runtime outcomes are unresolved. A timeout or service restart is not
proof of termination. Failed capture and unknown outcomes must retain capacity
reservations until reconciled by the actual authority/runtime boundary.

Before backing up the deployment, stop the services cleanly or use a
SQLite-aware backup procedure. Never copy a live database without its WAL and
call that a complete recovery image. Preserve the deployment identity and
credential files along with durable state.

### Remove enrollment and reconnect retained work

Run removal through the installed CLI while the controller remains running:

```sh
environments/zpm/bin/zpm --state state/zpm.sqlite3 remove owner/project
environments/zpm/bin/zpm --state state/zpm.sqlite3 status
```

Removal disables enrollment. The controller drains its owned workers and keeps
reservations until termination and capture are confirmed. Wait for zero reserved
workers before stopping the services. Authority data, worker history, captured
artifacts, configuration and credentials remain in the installation directory.
There is no implicit destructive removal or uninstall operation.

To reconnect the retained installation, use the same source pins and directory,
run `python3 manage.py verify`, then `bootstrap` and `start`. Completed
environments are reused. Repeating preparation is optional and leaves the
removed binding disabled. Explicitly re-enroll it before selecting new capacity:

```sh
environments/zpm/bin/zpm --state state/zpm.sqlite3 enroll config/repository.json
environments/zpm/bin/zpm --state state/zpm.sqlite3 target owner/project 2
environments/zpm/bin/zpm --state state/zpm.sqlite3 status
```

For schema 2, use that repository's `binding_file` from the prepare report or
generated instructions in place of `config/repository.json`. Removal leaves all
configured repositories in the sealed policy and credential files so retained
canonical work remains accessible.

The installed command trial checks this path after real execution and capture,
including a full service restart and byte-identical retained canonical artifacts.
Preparation interruption is covered separately by the process-level recovery
tests above. This runtime trial does not qualify a version upgrade.

The [command-based contract trial](managed-stack-trial.md) is separate from installation preparation. It must
run the actual authority and OpenJules services in separate processes, use a
real Docker container, validate canonical captured artifacts and exercise
capacity changes through ZPM's HTTP provider. Its controlled command does not
claim live model inference, delegated children, independent semantic review,
GitHub PR creation or merge authorization. Those remaining product capabilities
stay in their owning repositories' work packages.

The separate [model-backed installation trial](managed-model-stack-trial.md)
uses the real generated bundle, canonical authority, managed agent image and
broker with a deterministic localhost model fixture. It verifies a real code
change, independent patch testing, credential isolation and durable restart
without a live provider account.
