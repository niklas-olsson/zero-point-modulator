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
- The first managed profile runs one root attempt per worker incarnation, with
  no network access. It does not yet enable child delegation or connections to
  external model providers. A container running a controlled code-change trial
  is not evidence of live model inference or forge publication.

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

The destination must be new and outside all source checkouts. Preparation will
not overwrite an existing deployment. It writes the following private layout:

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
credentials in the host's secret mechanism. The baseline accepts only explicit
model environment references and does not inherit unrelated environment
credentials or `PYTHONPATH` into the services. Model references do not add a
network route to an otherwise network-isolated worker.

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

The [combined contract trial](managed-stack-trial.md) is separate from installation preparation. It must
run the actual authority and OpenJules services in separate processes, use a
real Docker container, validate canonical captured artifacts and exercise
capacity changes through ZPM's HTTP provider. Its controlled command does not
claim live model inference, delegated children, independent semantic review,
GitHub PR creation or merge authorization. Those remaining product capabilities
stay in their owning repositories' work packages.
