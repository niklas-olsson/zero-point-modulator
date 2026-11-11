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
- The three application checkouts and enrolled source checkout must be clean
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

## Prepare an installation

Start from [the operator configuration example](../examples/deployment.json).
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
version-upgrade or interrupted-preparation recovery operation. Keep an incomplete
directory for diagnosis; use a new destination for a fresh preparation. No
existing resources are overwritten or deleted.

| Path | Purpose |
| --- | --- |
| `manifest.json` | Approved normalized choices, exact source revisions and configuration integrity hashes |
| `config/polyphony.json` | Canonical repository, profile, base, attempt and acceptance policy |
| `config/openjules.json` | Approved Docker profile, lifecycle state, authority endpoint and secret references |
| `config/provider.json` | ZPM's scoped OpenJules provider configuration |
| `config/repository.json` | Initial ZPM repository enrollment |
| `secrets/` | Separate owner, reviewer, ingress, runtime and ZPM credentials |
| `state/` | Durable ZPM, authority and runtime journals and artifacts |
| `environments/` | Three isolated Python environments created by bootstrap |
| `manage.py` | Explicit bootstrap, foreground start, offline verify and online doctor commands |

The root and configuration directories use mode `0700`; secret files use mode
`0600`. No credential values appear in the manifest, normal configuration or
command arguments. Tokens are scoped to their relevant service and, for ingress
and runtime operations, to the declared deployment and canonical repository.
The owner and reviewer credentials identify the same `operator` principal.
They do not create two independent reviewers. The runtime is a separate
principal and cannot use its credential to accept its own work.

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
reference explicitly. The optional top-level `model_policy` is bound to the
same deployment, canonical repository and profile as the approved Docker
configuration. Omitting it keeps the command-only path and does not create a
model broker or choose a provider.

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

The [child installation trial](managed-children-stack-trial.md) records the
candidate component pins and required qualification gates. It exercises graceful
target-zero draining while the root and both children remain active. It retains the worker reservation until
final canonical captures exist, then verifies restart without new inference.

## Publish a final child composition

The pinned OpenJules child baseline also includes an explicit operator command
for [final artifact composition and draft PR publication](https://github.com/niklas-olsson/OpenJules/blob/3f0575364cc4b8d96e703f161652ec6fdfc8a6a1/docs/managed-publication.md).
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
`secrets/operator.json`, not the owner credential. Submit public task
requirements with a stable source and explicit execution-request key. Repeating
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

The installed command trial checks this path after real execution and capture,
including a full service restart and byte-identical retained canonical artifacts.
It does not qualify recovery of a partially prepared bundle or a version upgrade.

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
