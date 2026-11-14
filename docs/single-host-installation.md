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
- New preparation requires the Linux kernel and local filesystem to support
  `renameat2(RENAME_NOREPLACE)`, plus directory `fsync`. ZPM prefers the libc
  `renameat2` wrapper. If that symbol is absent, Linux x86-64 with 64-bit pointers
  and C `long` uses the same kernel operation through libc `syscall`; other ABIs
  require the wrapper. Unsupported atomic publication fails closed. The
  destination's existing parent directory must be trusted by the operator.
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
| `environments/openjules/openjules-managed-installation.json` | Private interpreter context created by bootstrap, bound to the sealed OpenJules configuration; it restricts legacy execution without adding task authority |
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

The wrapper-absent path uses the
[Linux x86-64 LP64 `renameat2` syscall number 316](https://github.com/torvalds/linux/blob/v6.12/arch/x86/entry/syscalls/syscall_64.tbl).
It preserves the directory-relative arguments, no-replace flag and libc error
number. A present wrapper is always used, including on other architectures.
An operation rejected by the kernel or filesystem is reported without trying
another rename operation. The fallback does not qualify x32, ARM, a browser
kernel, a complete Linux distribution or an application package set.

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
destination races. On Linux x86-64 LP64, those cases also run through the real
syscall with only the libc wrapper hidden. They cover publication contention
without the cooperative lock and injected directory-fsync errors before and
after publication. A post-publication fsync error reports failure and retains
the visible bundle; retry verifies and reuses that identity. These cases verify
process interruption and failure handling, not hardware power-loss behavior or
a version upgrade.

Do not publish, commit or copy the generated directory to a public location.
The input configuration contains secret references only. Keep actual model
credentials in the host's secret mechanism. The manager does not inherit
unrelated environment credentials or `PYTHONPATH` into the services. Existing
command-only profiles retain their explicit `environment_refs` behavior.
Model-backed profiles reject guest environment references and keep the provider
credential exclusively in the trusted OpenJules process.

### Native musl preparation trial

The [native musl trial](../tests/contracts/check_musl_preparation.py) runs the
preparation recovery cases with Alpine 3.22 CPython 3.12 on fresh ext2 and ext4
filesystems. Its [committed inputs](../tests/contracts/musl-preparation-inputs.json)
identify the exact minirootfs and main-repository APK set. It requires a Linux
x86-64 host with Python 3.12, Git, OpenSSL, Docker, e2fsprogs, `mount`, `losetup`
and `findmnt`. The invoking user needs Docker access and passwordless `sudo` for
the fresh owned loop mounts, their ownership and cleanup.

```sh
python -I -B tests/contracts/check_musl_preparation.py \
  --inputs tests/contracts/musl-preparation-inputs.json \
  --work-dir /absolute/fresh-owned/musl-preparation-trial
```

Acquisition is an explicit online phase with exact approved URLs, sizes and
hashes, a 128 MiB transfer cap and a 300-second deadline. Unavailable pinned APKs
fail closed; replacement inputs require an explicit reviewed manifest refresh,
and CI never resolves packages against a mutable index. APK execution and the
recovery tests then use Docker's `network=none`; local loopback remains available.
Each filesystem image is 256 MiB; test containers have a 512 MiB memory limit and
a PID limit of 128. Operations have a 15-minute deadline, including 90 seconds
reserved for cleanup, and bounded command output. A 3 GiB storage check counts
the logical bytes of work-directory files plus named Docker image and container
writable layer sizes. This sampled accounting can overshoot and can count shared
image layers twice; it is not a quota or a measurement of the whole daemon's
physical storage.

The retained `report.json` records the source, interpreter, libc, host kernel,
actual filesystems and outcomes, and binds the committed input manifest by its
hash. CI publishes only this report. The fixture qualifies this installation
primitive on the host kernel. It does not qualify Alpine's community Docker
packages, a complete managed stack, a browser kernel or a browser distribution's
storage and memory requirements. Exact candidate and resulting-main
qualification records are retained in
[ZPM #60](https://github.com/niklas-olsson/zero-point-modulator/issues/60).

## Managed interpreter context and bootstrap recovery

This contract requires the [pinned OpenJules context implementation](https://github.com/niklas-olsson/OpenJules/commit/0dd5bf4e42b3677c3f2f564655bc87a97c4bf973).
A valid generic source pin alone is insufficient. Require the component's CI and
all required ZPM jobs for the same exact integration set before promotion; retained
reports for earlier revisions do not qualify this new installation contract.

The preceding OpenJules component `80a51b867d12867a313539186b7c5d7346d54149` is the
merged result of [OpenJules #26](https://github.com/niklas-olsson/OpenJules/pull/26),
tree `0231a62c20d8ff66e0d5e9a3d5fc6b40e84909fa`. Its exact current-head and
relevant-base Python 3.11/3.12 suites each passed 612 tests and four subtests
without skips; [the complete qualification](https://github.com/niklas-olsson/OpenJules/pull/26#issuecomment-6808337505)
preserves the initial test-state failure and narrow correction. Those results
remain evidence for that component tree.

The preceding diagnostic integration candidate selected the
[merged diagnostics from OpenJules #27](https://github.com/niklas-olsson/OpenJules/pull/27),
`e50d501cc81975d25e367be3ba1d6de9169207c1`, tree
`61a52a5d448395ba6f168f387d504fb28ed77b26`. It retains that managed-context
boundary and reports one fixed unsupported-capability reason for the validated,
already-denied required initial-child-outcome extension. ZPM scopes that negative
diagnostic before retaining it in operator status. Other failures remain generic.
The component's [actual-main run](https://github.com/niklas-olsson/OpenJules/actions/runs/43119302563)
tested this exact merged revision: both Python 3.11 and 3.12 jobs passed 618 tests
and four subtests with the actual Docker gates. The
[retained CI disposition](https://github.com/niklas-olsson/OpenJules/pull/27#issuecomment-6809571567)
also preserves the earlier child-outcome failure; the later passing runs do not
establish that failure's cause or a runtime fix.

The preceding bounded socket component is
`57f62d1e108c61a21698c7d23936dc44e83b3092`, tree
`79fb000fa7d9ab1cdaf4bd39a0ffc506437acd9d`, from
[OpenJules #30](https://github.com/niklas-olsson/OpenJules/pull/30). Its
[actual-main run](https://github.com/niklas-olsson/OpenJules/actions/runs/43125104912)
passed 636 tests and four subtests on each Python version. The unchanged real
Docker reproducer now receives the existing UID-denial response while an
authorized provider call is held. A fixed acceptor and one serialized handler
retain the shared inference gate, finite queue, socket bounds and active service
ownership. The component result does not replace the installation trials.
The prior ZPM #28 candidate `d74628bcb45385a28b86b65cfb4b5b9c83a70b8e` remained
unqualified after both child jobs timed out on the first model-socket response
header during the fixed child verification. Those failures and the older
cause-unknown measurement failure remain tied to their recorded revisions.

The preceding normal and manual runtime references selected
`a1547a7743996d2db9866013135e5adb75c0ec7c`, tree
`497ea47626e8c94a65340733acf7f08a1fd8d9e6`, from
[OpenJules #31](https://github.com/niklas-olsson/OpenJules/pull/31). Its
[head and relevant-base qualification](https://github.com/niklas-olsson/OpenJules/pull/31#issuecomment-6811709699)
passed 703 tests and four subtests on each Python version. The
[actual-main run](https://github.com/niklas-olsson/OpenJules/actions/runs/43135106888)
then passed the same 703 tests and four subtests on Python 3.11 and 3.12, with
all Docker gates enabled and no skips. This is merged-component evidence.

This source adds the separately invoked bounded public-discussion participant,
pins MCP 1.30.0, and extracts the managed model broker's bounded HTTP transport
for shared use. Bootstrap installs the companion's requirements in its
dedicated private environment. The existing managed-service installation scenes run the
existing canonical work, recovery, cleanup and isolation scenes; they do not
start the new participant. Its native tests use named stdio, HTTP, SQLite and
signature fixtures. They do not supply or qualify the restricted Polyphony
conversation service, a complete installed conversation, a live provider
or live forge publication. The ordinary Polyphony service is not a fallback for
the restricted conversation profile.

The preceding installed delivery culminated in ZPM
`e3b15538f25b9c6bd2a4b46a9ab8ddca0c1e2853`, with OpenJules
`57f62d1e108c61a21698c7d23936dc44e83b3092` and Polyphony
`143d77c92ca8699ad7ccad4de98a540da6540a09`.
Its [head and relevant-base qualification](https://github.com/niklas-olsson/zero-point-modulator/pull/28#issuecomment-6810872810)
passed sixteen jobs, and its
[actual-main qualification](https://github.com/niklas-olsson/zero-point-modulator/pull/28#issuecomment-6811017999)
passed all eight jobs. These reports remain bound to that earlier delivery set.

The later ZPM #30 delivery at `0f131ed664d346f3c63f10c24f9d8e376a6581d3`
paired OpenJules `a1547a7743996d2db9866013135e5adb75c0ec7c` with that same
Polyphony revision.
Its [head and relevant-base qualification](https://github.com/niklas-olsson/zero-point-modulator/pull/30#issuecomment-6812183104)
passed sixteen jobs, and its [actual-main run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43138570851)
passed all eight jobs, including the unchanged 222-test suites and six installed
reports. That evidence remains bound to the recorded pair.

The initial child outcome authority first merged in Polyphony #122 as
`143d77c92ca8699ad7ccad4de98a540da6540a09`, tree
`0a1462d969b739c4928667c1cd099a9e009369e1`. Its
[normal current-base CI](https://github.com/niklas-olsson/keystone-polyphony/actions/runs/43125461545)
and [controller publication](https://github.com/niklas-olsson/keystone-polyphony/actions/runs/43125433077/attempts/2)
remain historical evidence for that exact revision.

The preceding positive Polyphony revision was
`dc18c3fb585e00a90706b04095bc278ee3ff1106`, tree
`ef5587ab91ed6bf205b7fbfaaa731e0442495d78`, the actual merged
[boundary documentation](https://github.com/niklas-olsson/keystone-polyphony/pull/134)
on [Polyphony #133](https://github.com/niklas-olsson/keystone-polyphony/pull/133)'s
legacy refusal implementation. Legacy advisory and experimental task or baton
paths refuse managed task IDs and managed backlog metadata. Caller-supplied
experimental labels do not confer managed acceptance or release authority.
The [component acceptance record](https://github.com/niklas-olsson/keystone-polyphony/issues/109#issuecomment-6812159757)
keeps component and controller evidence separate from a new installed-set result.

The preceding public-discussion component is
`5bdda3de5bc3b2f8afb8de9abc191661245fe8ba`, tree
`ffeeef5d45bf34af4a9c460b43777ed518b08fa5`, from
[OpenJules #32](https://github.com/niklas-olsson/OpenJules/pull/32).
It preserves the participant's raw configuration binding while allowing one
exact optional loopback bootstrap endpoint. Its
[actual-main component run](https://github.com/niklas-olsson/OpenJules/actions/runs/43140740562)
passed 726 tests and four subtests on both Python versions with all Docker gates
enabled and no skips.

The preceding cold-capable OpenJules revision was
`74429feda1f3991ce528678b74d6db5e3b3b250c`, tree
`9694e7450f12da3c162c62d220f782914902d7be`, from
[OpenJules #33](https://github.com/niklas-olsson/OpenJules/pull/33).
The exact current-head and relevant-base Python 3.11/3.12 suites each passed
849 tests and four subtests, including the real Docker cold-session case in each
of those four jobs.
The retained component record preserves the earlier configuration, observer and
parser-fixture failures and their narrow corrections. Component results do not
qualify a different installed set.

Cold clarification requires the explicit sealed
`session_protocol: cold-clarification-v1` option, initial outcomes, the trusted
default child command and at least two child slots. It does not enable itself
when the option is omitted, change the canonical four-field child policy, or
reuse a physical child. The [installed cold trial](managed-children-stack-trial.md#explicit-cold-clarification)
requires two fresh grants, separate charges and captures, the frozen predecessor
and earlier deadline, retained capacity, cleanup and restart. The child job
retains its default, initial-outcome, cold and acknowledged-failed scenes; the
managed-public attachment supplies a separate fifth report.

The original installed public-discussion delivery is ZPM
`43a18ac50b1e2798ff37b8a8113731af32769872`, paired with OpenJules
`5bdda3de5bc3b2f8afb8de9abc191661245fe8ba` and Polyphony
`0cf8b4772b04269505e3e1cd41324e41409b80f9`. Its
[actual-main managed-stack job](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43149758935/job/130956180524)
passed the original canonical scene and independent public-peer attachment.
The public scene used exactly three deterministic HTTP responses, preserved
the original signed proposal, decline and follow-up through replay, reaped all
44 retained leaders and preserved protected installation state. This evidence
stays bound to that preceding set; it is not cold-child discussion evidence.

The managed-public runtime delivered by OpenJules #42 is
`c925dee46765d2b22c9e38b55f614fc29f8068c1`, tree
`aefcaea19ad77963d5136b11056ad98ad9132a9e`, from
[OpenJules #42](https://github.com/niklas-olsson/OpenJules/pull/42).
Its [head and relevant-base qualification](https://github.com/niklas-olsson/OpenJules/pull/42#issuecomment-6818893339)
and [actual-main run](https://github.com/niklas-olsson/OpenJules/actions/runs/43184843146)
passed the complete 1285-test suite and 4 subtests on both supported
Python versions with the required Docker gates. The
linked component qualification record retains every earlier failed head, its
complete native logs and each separately reviewed correction. Later passing
results do not relabel those failed attempts. These are component results with
explicitly named authority, provider and conversation fixtures.

The earlier adopted-child reaping revision is
[`34a005a731ee4614ab1f105669378fcf814f092d`](https://github.com/niklas-olsson/OpenJules/commit/34a005a731ee4614ab1f105669378fcf814f092d),
tree `c95c192e9a28e40f110b4468e1a13410465253c9`, from
[OpenJules #45](https://github.com/niklas-olsson/OpenJules/pull/45).
Both runtime watchdogs reap exited children adopted by PID 1 using bounded,
nonblocking waits. Resource limits, process identities, authority and execution
deadlines are unchanged.

The preceding shared OpenJules image-acquisition pin is
[`394c1c22a740a80daf9dedd1b6a3e4712b2c5caf`](https://github.com/niklas-olsson/OpenJules/commit/394c1c22a740a80daf9dedd1b6a3e4712b2c5caf),
tree `b7877d3e55b0faf575e1e44d34cd5fc5c443594e`, from
[OpenJules #54](https://github.com/niklas-olsson/OpenJules/pull/54).
It retains the existing managed runtime and adds producer-owned finite
operator-local image acquisition, build, export and archive verification. The
[sealed bootstrap trial](#sealed-bootstrap-qualification) tests its verified
imported image with the three service environments. The entire changed consumer
set requires its own installed qualification before promotion.

The current shared OpenJules pin is
[`0dd5bf4e42b3677c3f2f564655bc87a97c4bf973`](https://github.com/niklas-olsson/OpenJules/commit/0dd5bf4e42b3677c3f2f564655bc87a97c4bf973),
tree `e92f616a633766341fab8ef896b5848d7e0fa22d`, from
[OpenJules #57](https://github.com/niklas-olsson/OpenJules/pull/57).
It adds the bounded typed capability refusal consumed by this ZPM revision.
Positive and unclassified replies omit the diagnostic, and the producer retains
its legacy human explanation during mixed-version operation. The paired HTTP
and installed historical-authority trials qualify the new producer/consumer
observation without changing capacity, task authority or recovery keys.
Image acquisition retains its separately approved input and helper revisions.
The complete installed set requires its own passing ZPM qualification.

The preceding shared OpenJules pin was
[`247fe1d4647510d158eb564594a406ef33fcafc8`](https://github.com/niklas-olsson/OpenJules/commit/247fe1d4647510d158eb564594a406ef33fcafc8),
tree `9d6bdc84891c836f19bae5a97f150e8c6bb0bc1d`, from
[OpenJules #46](https://github.com/niklas-olsson/OpenJules/pull/46).
Its managed image compiles checked-hash standard-library bytecode at normal
optimization during the image build. Runtime interpreters can reuse those
root-owned caches from the read-only image. The image retains
`PYTHONDONTWRITEBYTECODE=1`; its read-only root protects the standard library
for isolated commands. Managed agent source loading is unchanged. The native image test
requires fresh isolated imports with source-compilation fallback refused.
The confined suite, all installed scenes and a fresh parent-and-two-child
execution must qualify this consumer set separately; component cache evidence
does not establish its completion time or deadline margin.

This runtime connects the approved managed-public descriptor to the original
root and child Unix identities, shared inference allowance, restricted
conversation profile and frozen canonical capture. Omitted policy keeps the
existing behavior. The [installed managed-public scene](managed-children-stack-trial.md#managed-public-discussion-during-private-work)
requires real installed Polyphony authority and transport, two confined children,
an independently running peer, original signed effects, scoped private-read
controls, owned cleanup and full service reopening. Every new installed set
requires its report and the complete required workflow. Neither component
fixtures nor the independent public-peer scene substitute for that result.

The preceding fully qualified installed set is ZPM
`55dfd6a8f275a6b649fda86b2fe6f48b2321daf5` with OpenJules
`74429feda1f3991ce528678b74d6db5e3b3b250c` and Polyphony
`0cf8b4772b04269505e3e1cd41324e41409b80f9`. Its
[head, relevant-base and actual-main evidence](https://github.com/niklas-olsson/zero-point-modulator/pull/36#issuecomment-6816512407)
contains all 24 successful jobs; [actual-main run43165296956](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43165296956)
confirms the same 273 passing test identities on both Python versions, four child
scenes and the independent public-peer scene. It preserves the unchanged
qualified runtime from `3abded40` and adds only the historical cold-measurement
record and its link. Earlier results retain their original revisions. These
reports do not qualify the new producer or close the separate completed-cycle
measurement criterion.

The preceding positive Polyphony revision is
`0cf8b4772b04269505e3e1cd41324e41409b80f9`, tree
`ca53939281041c98e1d36294078d183862c71cbd`, from
[Polyphony #135](https://github.com/niklas-olsson/keystone-polyphony/pull/135).
It provides the separately invoked restricted public-conversation profile with
durable signed records, bounded stdio/native transport and fixed child
environments. The [native component qualification](https://github.com/niklas-olsson/keystone-polyphony/actions/runs/43143564993)
passed 1320 tests without skips on the identical delivered tree. That component
evidence is distinct from the
[independent public-discussion attachment](public-discussion-stack-trial.md),
which reuses the stopped managed bundle and must produce its own passing report.
Those component and independent-peer results do not establish live-provider
judgment or replace the managed root-and-child report.

The current shared Polyphony pin is
`6480e36355bf0d5de4e25a560d40e665fd76038c`, tree
`3e8841f995df388b5d9c108ead1b677355c8ca07`, from
[Polyphony #141](https://github.com/niklas-olsson/keystone-polyphony/pull/141).
Its [exact-merge CI](https://github.com/niklas-olsson/keystone-polyphony/actions/runs/43577103675)
passed before the supported merge controller confirmed publication. The maintained
grant implementation and service now use `delegated_grants` and
`delegated_grant_service`; legacy imports, exception identities, v1 routes,
capabilities and policy remain compatible.

All normal and manual checkouts, the publisher's source assertion and the
historical-admission probe select this operative revision. The probe retains
historical `473df5a0` as the negative revision and validates both source trees and
imported bytes, including the new maintained modules and their absence on the
historical source. It no longer assumes the compatibility wrapper contains the
implementation or that current modules match older blobs. Existing canonical
scenes retain their assertions, while the
separate attachment starts two independent installed public peers with exactly
three deterministic provider requests and original signed remote receipt.
Every changed set requires its own complete ZPM qualification; historical
reports retain their original component identities.

Every change to the integration set must pass all required ZPM jobs before promotion.
Earlier `9e6aaa49` / OpenJules `d3d50b32` runtime reports, the 612-test component
results and the [measurement set with OpenJules `80a51b86`](https://github.com/niklas-olsson/zero-point-modulator/pull/27#issuecomment-6809671165)
remain bound to their recorded revisions; they do not qualify the changed set.

A prepared bundle is still offline configuration. Bootstrap installs its three
isolated environments and binds the dedicated OpenJules interpreter to the
original sealed `config/openjules.json`. The private selector has one fixed name
under that interpreter's prefix. It contains only version 1, the canonical
configuration path and its SHA-256, with a 4 KiB limit. It carries no credentials,
repository policy or authority grants.

Before bootstrap marks OpenJules complete, it invokes the actual installed
interpreter from the approved OpenJules checkout:

`python -m src.worker.execution_context --probe-managed-installation`

The probe receives the credential-free bootstrap environment. It imports only
the companion's local context helper, validates the original binding and calls
the same predicate that refuses legacy execution. ZPM requires the exact
versioned response, the SHA-256 of its expected selector bytes and
`legacy_execution_allowed: false`. Missing or older helper implementations,
malformed responses, changed bindings and timeouts fail closed. The probe has
a ten-second execution bound, at most 4 KiB of stdout and a separate two-second
owned-process cleanup bound. Child stderr is discarded and failure messages do
not echo private paths or subprocess output. No Docker, provider, model or
canonical-authority request is part of this probe.

The same check runs before a completed environment is reused by bootstrap and
before start or doctor can report operational readiness. It does not replace
doctor's authenticated capability check. Fresh `prepare` and offline `verify`
remain valid without installed environments and do not claim managed-context or
runtime readiness. Merely writing a selector cannot qualify an older OpenJules
revision that ignores it.

Bootstrap publishes the selector and OpenJules completion marker through
private temporary files and atomic, no-replace hard links on the same local
filesystem. Each published file and its directory are flushed. This requires
local regular-file hard-link and directory-fsync support in addition to the
preparation requirements above. An interruption leaves each final file absent
or complete. A new incomplete bootstrap may publish its original absent
selector or reuse identical bytes. It never overwrites a different file or
adopts a foreign path; abandoned private temporary files remain available for
diagnosis. The tests exercise process crashes, not hardware power loss.

A completed environment with a missing, malformed, symlinked, nonprivate or
changed selector refuses reuse and startup. Restore the retained original
installation from trusted state rather than deleting completion markers to
force adoption. This is not an automatic repair or version-upgrade operation.
An old source-pinned bundle keeps its original contract; use a separately
approved new bundle for different source revisions. Pause, removal and
re-enrollment retain the context, credentials, journals and canonical policies.

Within this managed interpreter, legacy raw-task, review and other execution
entry points refuse before their owned effects. Canonical managed service,
publication and cleanup entry points retain their existing authority checks.
The selector does not restrict an approved host's alternate canonical
configuration to a new scope list. Separate unbound standalone interpreters
keep their ordinary behavior. Deleting private host state or choosing another
interpreter is an operator action outside the request-isolation boundary.

The existing [installed command trial](managed-stack-trial.md) invokes the real
legacy CLI before services start, requires its explicit refusal, and compares
retained state hashes and the complete Docker container/volume inventory before
and afterward. It then runs the unchanged positive canonical execution,
capture, outage and recovery scenario. Its installed evidence must qualify the
reviewed OpenJules producer and ZPM consumer revisions together; unit protocol
fixtures alone do not demonstrate that integration.

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

## Opt in to a managed public discussion profile

The optional `child_policy.public_discussion` block selects an approved public
topic and a finite set of host-provided personas, instructions and invitations.
It requires an OpenJules revision implementing `managed-public-pull-v1`, the
matching managed-agent image, an existing `model_policy`, `outcome_protocol` set to
`initial-v1`, the default child runner and `max_children` of at least two.
Use a new approved profile and worker incarnation. The option cannot coexist
with `session_protocol` in the same profile. A schema-2 installation can carry
separate public, cold-session and omitted-option profiles.

For example, add this block to that profile's existing child policy:

```json
{
  "public_discussion": {
    "protocol": "managed-public-pull-v1",
    "public_approved": true,
    "topic": "an-operator-approved-public-topic",
    "server": {
      "scope_key_file": "/srv/approved-public/original-scope.key"
    },
    "personas": {
      "reviewer": "Contribute independent reasoning about the approved public question."
    },
    "instructions": {
      "question": "Discuss the explicitly approved public question."
    },
    "invitations": {}
  }
}
```

All seven public-block fields are required. `public_approved` must be the JSON
boolean `true`. Omitting the whole block preserves the existing policy and
generated configuration; an explicit `null` is invalid. Raw deployment input
and retained manifests containing this option reject duplicate JSON keys and
nonfinite numbers before preparation or service operations.

| Field | Accepted shape |
| --- | --- |
| `protocol` | Exactly `managed-public-pull-v1` |
| `topic` | Nonempty text, at most 256 UTF-8 bytes, without NUL or control characters |
| `personas` | One to three approved keys mapped to public text |
| `instructions` | One to eight approved keys mapped to public text |
| `invitations` | Zero or one approved key mapped to exactly `target` and `content`; target is a 16-character lowercase hexadecimal public author |
| `server` | Original private `scope_key_file`, optional exact `scope_key_sha256` and optional literal-loopback `bootstrap` |

Approval keys match `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`. Persona, instruction
and invitation content must contain non-whitespace text and fit within 4,096
UTF-8 bytes each. ZPM preserves their exact spelling and whitespace. The
generated public block must also fit within 65,536 bytes of the existing
ASCII-escaped canonical child-policy JSON. This leaves existing canonical
fingerprints unchanged when the option is omitted.

The original scope path must be canonical and absolute, with no symlink
component. The file must belong to the effective user running these commands, have exactly
mode 0400 or 0600 and one hard link, and contain 1–4,096 valid UTF-8 bytes without
NUL. Preparation checks the original directory and file descriptors, reads
bounded bytes, verifies the optional supplied SHA256, then copies those same
checked bytes into a derived mode-0600 file under the bundle's `secrets`
directory. It preserves trailing newlines and other valid bytes exactly.
Normalized configuration retains only the original path and hash. Neither the
scope value nor caller-selected Python or checkout paths enter that normalized
server block.

The generated OpenJules server selects the original installed Polyphony
interpreter at `environments/polyphony/bin/python`, the approved immutable
Polyphony checkout, and the sealed scope copy and hash. Each copy name binds
the deployment, canonical repository and profile. The original virtualenv
launch path is retained even when that executable is a symlink. Optional
bootstrap is exactly `{"host": "127.0.0.1", "port": 41001}` with an explicit
integer port from 1 through 65,535; no other host spelling is accepted.

**Retain the original external scope file for the installation's lifetime.**
Verify, bootstrap, start, doctor and repeated preparation require that original
file to remain private, readable and hash-identical. Runtime conversation
processes read the sealed copy. A missing, changed, linked or unreadable
original or copy refuses operation and preserves the retained installation.
Repeated preparation never rotates the scope or credentials, adopts a changed
profile, restores a removed enrollment, or rewrites captures.

Supply the root command explicitly with this fixed prefix and all three socket
pairs. Flag order may vary, but flags cannot repeat:

```json
[
  "python3", "-I", "-S", "/opt/openjules/managed_root_runner.py",
  "--socket", "/run/openjules-model/model.sock",
  "--control-socket", "/run/openjules-model/control.sock",
  "--public-socket", "/run/openjules-model/public.sock"
]
```

The only optional pairs are `--max-turns` with an integer from 1 through
1,000, `--control-timeout-seconds` with a finite positive value at most 120,
and `--verify-json` with a JSON list of at most 128 nonempty, NUL-free strings
of at most 4,096 UTF-8 bytes each. Existing overall command bounds still apply.
The child command remains the default trusted managed runner. ZPM does not
rewrite an arbitrary root command to enable public tools.

The option changes the complete OpenJules policy fingerprint and sealed
configuration. Polyphony still receives only the existing child count, seconds,
turn and capability projection, alongside its existing root requirements.
Existing root model allowances, canonical child grants and ZPM worker capacity
continue to bound execution. Public contributions confer no execution,
verification or publication authority.

Preparation is an offline configuration check. Qualification must exercise the
exact producer, image and installed component set before promotion through the
[managed-public trial](managed-children-stack-trial.md#managed-public-discussion-during-private-work).
The independent public-discussion trial and its historical reports retain
their recorded scope and revisions. This configuration option alone does not
prove a managed public turn, child participation or delivery to a live peer.

## Clean a captured worker's runtime resources

The pinned OpenJules runtime provides an
[explicit host cleanup command](https://github.com/niklas-olsson/OpenJules/blob/0dd5bf4e42b3677c3f2f564655bc87a97c4bf973/docs/managed-docker-runtime.md#explicit-host-cleanup).
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
for [final artifact composition and draft PR publication](https://github.com/niklas-olsson/OpenJules/blob/0dd5bf4e42b3677c3f2f564655bc87a97c4bf973/docs/managed-publication.md).
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

The manual [Managed publication qualification workflow](../.github/workflows/managed-publication.yml)
runs execution without a forge credential before entering its separate publisher
step. Its [first actual run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43266932548)
failed in the broad `root-and-children` stage and skipped publication. That
report does not identify the failing operation or its cause; elapsed time and
later diagnostic changes cannot establish either retroactively.

The separate manual [Managed publication execution qualification workflow](../.github/workflows/managed-publication-execution.yml)
uses the same installed `execute` harness, exact component pins, image build,
grants and model allowances with repository contents read permission only. It
runs the real isolated parent and two children with deterministic model replies,
performs the configured full repository unit suite, and retains only the safe
`execution-report.json` or `failure-report.json`. It invokes no publisher; the
execution process receives no forge publication credential. Checkout actions
retain their normal read-only GitHub token context. Both manual workflows share
one concurrency group without cancelling an active run.

Its [first execution-only run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43280715096)
on `c72e946e61e2f31017018b248d86807ede9437bd` reported `admission-close` / `key`
before owned cleanup. Source inspection identified a harness access to the
nonexistent `effective_workers` field; `Controller.plan()` exposes
`effective_target`. The corrected predicate reads that field while still
requiring exactly one reservation and canonical `admission_closed is True`.
The failed run did not qualify combined verification, final capture or reaping.
The [next new-source execution-only run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43285397910)
on `6e422b62bd6e3c8effab5c6bc027d53a49f1aff8` advanced through admission and
root-termination polling, then reported `root-capture` / `assertion`. Its local
fixture observation contained all ten accepted requests and no fixture error.
That report did not retain the exact assertion or verification result, so the
underlying failure remains undetermined and execution qualification is incomplete.
The [following execution-only run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43295755993)
on `eb4bc470c3e7138e2f7d680a8abfb80e95a48e1d` retained an original, hash-bound
capture with a complete 412-test suite summary: 57 failures and 34 errors in
62.959 seconds. Those counts can include subtests. The canonical artifacts were
complete but no candidate was observed; the observed assertion was
`candidate-publication-deadline`. Its cancelled receipt, null exit codes and
zero observed configured-verification events do not establish a timeout or the
cause of cancellation. The bounded report identifies sixteen approved failing
methods, not their private exception details or every failed method.

The full-suite verifier now creates a fresh mode-0700 temporary directory under
the original owned `/workspace/run`, outside the candidate checkout. Discovery
uses that directory, and the suite subprocess inherits the existing environment
with only `TMPDIR` selected explicitly. Installation fixtures copy and execute
their own virtual-environment interpreters, so the container's deliberately
`noexec` `/tmp` is unsuitable for those temporary executables. The verifier
refuses preexisting or unsafe temporary paths. After a successful complete suite,
it checks the original directory identities and removes only the empty temporary
root; failure or unexpected remaining files retain the private directory and log.
The runtime mount, identities, capabilities, network policy and execution bounds
are unchanged. This fixes that fixture/environment incompatibility; it does not
establish the cause of the other process or preparation failures in the recorded
run, nor does it assign a cause to earlier runs. A new confined execution at the
reviewed revision must still pass every original acceptance check.

The later [execution-only run on `90cc7174`](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43302946878)
still failed: its captured full suite reported 415 tests, 52 failures and 25
errors. The cause of those remaining failures was not established by its capped
list of test identifiers.

The existing managed-model CI job now runs the complete public-source suite
through the pinned OpenJules Docker producer and the same managed-agent image.
This diagnostic uses a fresh, explicitly synthetic readiness fixture, no
authority, broker, child grants or forge credential. The native source export,
root UID, non-child watchdog, private temporary directory and default isolation
and resource limits are retained. Its 110-second suite and 120-second command
guards do not extend the publication trial's bounds. All discovered test identities
must execute once; skips, incomplete execution or failures prevent a pass.
Guest preparation preserves the reviewed checkout, including publication files
already present in source. Unit tests construct their own temporary repositories
and publication fixtures; the guest's private temporary directory remains outside
the checkout.

Only `confined-suite-trial/report.json` is exported. It contains bounded
exception categories, numeric error/exit codes, frame coordinates checked
against public source, and resource counters. Atomic checkpoints also retain
started and completed counts, the active approved test identity, elapsed suite
time and at most sixteen slowest completed approved identities with wall times.
These bounded fields locate unfinished work without exposing test messages or
arguments. Counts observe unittest start/stop callbacks; a checkpoint with every
test stopped is not a completed suite or successful process exit. Fixed booleans
also record the interpreter's bytecode-write setting and existing cache-file
presence for the already imported `json`, `subprocess`, `tempfile` and `unittest`
modules, without paths or file contents. Resource scans remain outside the
successful test start/stop path. Cache presence does not establish cache validity
or use. Missing kernel observations remain unknown. The suite projection is a
fresh bounded read from the stopped fixture,
separate from the native capture. Raw logs and captures stay private. Cleanup
checks exact native container/volume identities and confirmed removal; uncertain
cleanup fails. This fixture has fewer processes than a managed root and two
children and uses the non-child watchdog. A pass does not qualify their combined
deadlines, original failure cause, canonical candidate or publication outcome.

The first [HEAD diagnostic](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43312151104/job/131511426492)
and [proposed-merge diagnostic](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43312194656/job/131511595483)
with OpenJules `c925dee46765d2b22c9e38b55f614fc29f8068c1` both executed all 422
tests with no skips and 77 failure observations, including subtests. Initial
PID usage was four with zero zombies; final usage reached the unchanged
128-PID ceiling, with 123 zombies and 475 PID-ceiling rejection events. Bounded
source coordinates also identified subprocess failures with error number 11.
Both suites completed their test inventory but did not exit before the
110-second process guard. Native capture, termination and exact fixture cleanup
were confirmed. This records the reproduced producer defect; earlier private
operations retain their original evidence limits.

With the merged reaping runtime, the
[`33f53a95` HEAD diagnostic](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43316687385/job/131527195970)
passed all 422 tests with no skips or failures in 106.599 seconds and exited
normally. PID usage remained four, with no observed zombies or PID-ceiling
rejections. The matching
[`ee9595ea` proposed-merge diagnostic](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43316695748/job/131527231504)
reached the unchanged 110-second suite guard without a completion record or
observed failure details. Both used OpenJules
`34a005a731ee4614ab1f105669378fcf814f092d` and confirmed native capture,
termination and exact fixture cleanup. The added progress and timing fields
locate remaining slow work; these results do not qualify the combined revision
set or justify increasing its bounds.

The later
[`014b7e0f` HEAD diagnostic](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43319388209/job/131536512368)
and [`46da47c1` proposed-merge diagnostic](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43319397886/job/131536578717)
both used OpenJules `34a005a731ee4614ab1f105669378fcf814f092d` and observed
bytecode writing disabled with no cache files for the four recorded
standard-library modules. HEAD passed all 424 tests in 90.709 seconds with no
failures or skips. The proposal reached the unchanged guard after 406 start
callbacks and 405 stop callbacks; its last progress checkpoint was at 108.268
seconds with no observed test failure. Both retained native capture, fencing,
termination and exact fixture cleanup. These measurements select the image-cache
improvement for qualification; they do not establish its effect before a new
complete run at the updated producer revision.

Dispatch this execution-only workflow from its reviewed main revision as a new
source qualification. It is not a rerun or recovery of the original publication
run `43266932548`; that run's cause remains unknown and its private execution
state remains unavailable. An execution pass establishes no forge outcome,
independent PR review or live-provider behavior. Private captures, handoff and
journals are not uploaded and cannot be reused for publication by a later job.
A future full publication trial is a separate authorized operation with its own
execution and same-job original-journal replay. It still requires an owner
decision on the repository's Actions PR creation permission; this read-only
workflow changes no publication privilege.

An execution failure now retains an optional `failure_observation`: the first
observed operation and exception category from fixed allowlists. When observed
before owned-process cleanup, it also records bounded local fixture turn/request
counts, the two-child observation flag and a fixed category for the first fixture
error. These are non-atomic diagnostic observations, not evidence that canonical
grants, execution or capture completed. Unknown values are `null` or `other`;
unavailable diagnostics cannot replace the original failure. A failure first
observed after a cleanup attempt has an explicit timing label and no fixture
snapshot; that label does not prove successful reaping.

For `root-capture` only, the optional observation also records a fixed assertion
category and a bounded summary of the latest existing canonical snapshot read.
Invalid or unavailable collections retain `null` dependent fields; they do not
become observed absence. At the first failure, read-only local inspection may
summarize the original runtime receipt and its hash-bound capture. The worker,
attempt, approved base, configured profile fingerprint and original manifest
must agree before archive contents are interpreted. Busy, missing, changed or
unsafe evidence remains unavailable without another read attempt.

Only bounded verification states, nullable exit codes, numeric suite frames and
up to sixteen failing test identities are exported. Test identities must match
ASTs whose bytes match the approved Git base; this does not import or discover
tests. Subtest parameters, messages and tracebacks remain private. A partial
suite can report observed progress and its last approved test identity without
claiming that test completed. Duplicate, out-of-order or truncated summaries
are not an unambiguous complete result. In-range `<string>` frame numbers are
observed numbers, not proof that a particular configured-script assertion ran.
Receipt state `completed` and a nullable exit code do not establish success or
timeout. These non-atomic diagnostic reads establish no recovery or reaping.

Exception text, unknown exception names, private paths, provider/tool payloads,
raw fixture errors and captures remain private. The observer adds no authority,
runtime, model or forge requests, polling, retries or execution allowance. Its
failure-only reads bound receipt JSON to 128 KiB, the root log to 256 KiB, the
complete suite log to 2,000,000 bytes and the archive to the smaller of the
approved profile limit and 64 MiB. Approved test ASTs are limited to 64 files,
256 KiB per file, 2 MiB total and 1,024 identities. A failed trial does not authorize an
automatic rerun or a replacement operation; reconcile the original operation
and actual forge state first.

The pinned standalone legacy webhook also refuses run commands delivered in a
PR's general conversation with HTTP 409
`pull_request_comment_requires_review_event`, before task construction or
background dispatch. Its existing review-event guard remains separate. The
generated managed service exposes no legacy webhook route. This refusal does
not establish convergence of every standalone ingress or enable public managed
conversation; see [OpenJules #23](https://github.com/niklas-olsson/OpenJules/pull/23).

## Installation-admission contract fixtures

The first data-only slice of [#56](https://github.com/niklas-olsson/zero-point-modulator/issues/56)
is available in `zpm.installation_admission`. It defines the private
`zpm-installation-admission/v1` receipt and compares selected scope, base,
authority profile digest and required protocol facts from producer examples.
It does not replace native policy validators or inspect installed dependencies.
Existing `prepare`, `verify`, bootstrap, service startup and operative component
pins do not call this API. There is no new CLI or persisted configuration ledger.

| Function | Inputs and result |
| --- | --- |
| `prepare_receipt(selection_bytes, inputs, receipt_id=...)` | Bind exact explicitly supplied selection and private input bytes, returning a private `offline-prepared` receipt. The caller supplies an opaque random UUID4; no private digest becomes a public identifier. |
| `encode(receipt)` | Stable ASCII JSON, sorted keys, compact separators and final LF. The caller retains these bytes privately if needed; the function does not write files. |
| `compare_fixtures(receipt_bytes, selection_bytes, inputs, observations)` | Refuse a changed selection/input binding, then compare bounded per-producer examples. Return only `evidence: contract-fixture` and a separate nullable `fixture_compatible` result. |
| `public_summary(report)` | Check the closed fixture-status report and return its safe allowlisted projection. It excludes all selected input/configuration/credential hashes, source identities, profile digests, private values and raw producer diagnostics. |

The selection is bounded UTF-8 JSON with exactly `schema_version: 1`,
`deployment_id`, `installation_plan_digest`, `components` and `profiles`.
`components` has exactly `openjules` and `keystone-polyphony`; each selects
`repository`, full `source_revision`, `validation_contract` and
`contract_digest`. These are expected identities, **not verified source
attestations**. Profiles contain exactly canonical `repository`, `profile`,
immutable `base`, existing producer-owned `profile_digest` and `capabilities`,
sorted uniquely by repository/profile. The consumer compares that existing
digest; it never reconstructs native profile, child, model or reviewer policy.

`inputs` is a separate in-memory mapping of explicitly supplied bytes. OpenJules
has `configuration` and optional `scope_declarations`; Polyphony has optional
`managed_policy` and required `credentials`. Optional absent bytes are `None`.
The API hashes exact bytes, including whitespace, and records each binding
separately in the private receipt. It does not open credential files or resolve
environment references. A changed policy, credential input, source selection,
contract digest or even selection whitespace invalidates reuse of the old
receipt. Input hashes establish correlation only, not input authenticity.

An `observations` mapping supplies each service's UTF-8 fixture envelope or
`None`. Every envelope has exactly `source_revision`, `contract_digest`,
`input_digests` and the producer-specific `result`. These wrapper fields are
checked against the private receipt. They are caller-supplied example data and
do not prove a producer command executed. OpenJules results also bind their own
exact configuration/scope digests. Polyphony results intentionally have no input
hash or self-reported source fields. Its explicit enabled/disabled state is
preserved: a valid disabled authority cannot meet required managed support.

Missing, malformed, unsupported or differently bound results stay unvalidated.
Well-formed invalid/disabled examples or mismatched scope/base/profile/capability
facts cannot produce a fixture match. A selected public-conversation requirement
remains unvalidated because the proposed managed installation descriptor does
not provide that independent contract. A child-grant capability is never treated
as actual child execution or conversation support. Error reports contain only
fixed consumer reason codes, never raw producer diagnostics or token-map keys.

Receipt `components.*.valid` and `compatible` remain null. The phase is always
`offline-prepared`; `source_identity_verified`, `native_validation_performed`,
`live_capabilities_checked` and `execution_qualified` are always false. Passing
caller flags or a fixture cannot promote those fields. This slice deliberately
has no `native-config-validated` path. A fixture match supplies no launch,
publication or task permission, availability, installed readiness or release
evidence. It cannot release an uncertain reservation.

Selection/receipt documents are bounded to 131,072 bytes, each producer example
to 8,000,000 bytes, nesting to 24 levels and the composition to 100 profiles,
matching the installer limit rather than changing a native producer's limit.
Configuration, policy and credential inputs each have a 1,000,000-byte limit;
scope declarations have a 65,536-byte limit. Strict JSON rejects duplicate
fields, nonfinite values and invalid UTF-8. Closed objects and exact scalar
types reject Boolean/integer aliases. Encoding/parsing/hashing is local work;
there is no filesystem, subprocess, network, clock or model operation.

### Fixture provenance and remaining integration

The attributed [fixture set](../tests/fixtures/installation-admission/README.md)
retains the exact OpenJules artifact from merged producer revision
`77e1191bc373c9a7bd999eaf33bc5a0b54d8323c` and selected unchanged conformance
vectors. Its SHA-256 is
`1eaa70ae5ea2b28259c7f7f1884bb93335f7f51cd3dc1edbaf58710f9644573b`, calculated
over **exact committed artifact bytes**, not canonicalized JSON. This explicit
producer rule differs from the canonical JSON artifact encoding proposed by
Polyphony [#139](https://github.com/niklas-olsson/keystone-polyphony/issues/139#issuecomment-6869132293).
Polyphony examples here are synthetic definition fixtures with synthetic
digests, not outputs of a delivered native validator or static contract.

Run the pure examples and negative/no-effect checks with:

```sh
python -m unittest tests.test_installation_admission -v
```

Later integration must independently verify pinned producer source and contract
bytes, explicitly bootstrap isolated interpreters, invoke their actual native
commands against the retained selected inputs, and bind the outputs before any
service/runtime state creation. Source/result fields supplied by a process are
insufficient attestation. Paired native and affected installed task/capture/drain/
restart evidence remains outstanding in #56, OpenJules #50 and Polyphony #139.
That integration must preserve legacy preparation and retry identities. The
fixture API does not enable it by declaring success.

## Install dependencies and start

From the generated directory:

```sh
python3 manage.py bootstrap
python3 manage.py start
```

Bootstrap is the explicit dependency-installing step. Without `dependency_plan`,
it uses each repository's
declared dependency file and records the resolved versions in that service's
`environments/<service>/resolved-requirements.txt`. Application revisions are
pinned. Existing upstream dependency ranges are not represented as a complete
reproducible dependency lockfile. Re-running bootstrap retains completed
environments instead of silently upgrading them.

### Sealed local Python artifacts

The optional `dependency_plan` field in deployment schema 1 or 2 selects
`sealed-local-wheels-v1`. Omission retains `ranged-install`. This opt-in is for a
fresh installation; it does not convert or upgrade an existing bundle. It
supports explicitly declared Linux CPython 3.11/3.12 tuples. In particular,
Alpine/Python 3.14 and a Terrarium browser image are not qualified by this path.

Add the [reference object](../examples/dependency-plan-reference.json) at the
top level of the deployment configuration, replacing its placeholder paths and
zero hash with the approved values:

```json
"dependency_plan": {
  "manifest": "/absolute/approved/python-artifacts/dependencies.json",
  "sha256": "<SHA-256 of the exact manifest bytes>",
  "artifacts": "/absolute/approved/python-artifacts/wheels"
}
```

The [closed structural schema](../examples/dependency-plan.schema.json) describes
the manifest. The installer additionally enforces the semantic rules below.
Duplicate JSON keys, unknown fields and nonfinite values fail admission.

| Field | Required value and meaning |
| --- | --- |
| `schema_version` | Integer `1`. This version is separate from deployment and lifecycle-wire versions. |
| `interpreter` | Exact `implementation`, three-part `version`, `cache_tag`, `abi` (Python `SOABI`) and `platform` (`sysconfig.get_platform()`). Bootstrap checks its interpreter and each actual service interpreter. |
| `seed` | `method: "sealed-pip-wheel"` and the filename of one admitted pip wheel present in all three environment lists. No host ensurepip package is used. |
| `artifacts` | A finite list of unique safe wheel filenames, byte sizes, SHA-256 hashes, normalized distribution names and metadata versions. No URLs, sdists or arbitrary pip options. Wheel metadata must match the approved identity. |
| `environments` | Exactly `zpm`, `openjules` and `polyphony`, each with its full `source_revision`, `dependency_files` digests and complete `wheels` list. Each list has one version per distribution. Separate environments may select different versions. |
| `dependency_files` | Exactly `pyproject.toml` for ZPM and exactly `requirements.txt` for each producer. Simple package requirements, extras and markers are checked against installed metadata. Requirement includes, URLs and package-manager options are unsupported. |

The manifest is at most 1,000,000 bytes. It admits at most 512 wheels, 128 MiB
per wheel and 1 GiB in total; every wheel must be used. These are artifact input
bounds, not a guarantee of installed disk use or package-code confinement.
ZPM's build requires admitted `pip`, `setuptools` and `wheel` distributions and
any dependencies they need. The current setuptools backend is supported;
alternate backends and backend paths are refused. The whole tracked ZPM source
is bounded to 2,048 regular files and 32 MiB, verified against its Git blob
identities and copied into the private bundle during preparation.

Acquire and review artifacts separately. Preparation and verification read
bytes and metadata only. They never create a virtual environment, import a
producer, invoke package/build hooks or acquire packages. They reject linked,
nonregular, changed or escaping inputs and copy hash-verified opened bytes into
private read-only bundle files. Retain the original manifest, wheelhouse and
clean source checkouts: all subsequent verify/bootstrap/start/doctor operations
recheck their original identities as well as the sealed copies. Moving or
deleting these inputs is not a supported migration.

At explicit bootstrap, each environment starts with `venv --without-pip`.
Its isolated interpreter runs the exact sealed pip wheel. All third-party
installs use only the admitted hash-pinned local wheels, `--no-index`,
`--no-deps`, `--require-hashes`, `--only-binary=:all:` and `--no-cache-dir`.
The installer strips ambient pip variables, sets `PIP_CONFIG_FILE=/dev/null`
and uses private HOME and temporary directories. It does not use pip's
`--isolated` option, which would ignore that configuration-file setting.
ZPM builds from its sealed source copy with the exact admitted build toolset
and `--no-build-isolation`; the original checkout remains clean. Its built wheel
and source/toolset/hash record remain in `environments/zpm/sealed-build/`.

Before completion, each interpreter passes `pip check`, declared-root and
extra dependency checks, its native service import and exact installed-set
validation. Installed RECORD hashes and an aggregate digest of each
distribution's actual files are checked; generated bytecode is excluded.
OpenJules also passes its existing managed-context probe before completion.
`sealed-complete.json` records the admitted plan, source/interpreter identity,
installed versions/content digests and the ZPM build-record digest. The existing
`bootstrap-complete.json` format remains compatible. Reuse checks these receipts
and actual installed contents before accepting an environment. A version list
alone is not accepted as the original installation.

Bootstrap writes its original intent before changing an environment. A stopped
or interrupted attempt can resume only that same plan and interpreter tuple.
The Linux command guardian inherits the manager lock, monitors the manager's
private pipe, kills its owned package-process group, and reaps adopted children
before releasing the lock. A descendant that creates another session is outside
that group signal; the guardian retains the lock until that descendant exits.
A killed manager therefore cannot allow a
second installer while its old package command still writes. Unconfirmed
kernel cleanup retains the lock and does not produce completion. Packages and
build hooks execute with the installation account's authority; approved wheel
identity is not a sandbox against malicious package code or that trusted UID.

Individual commands have 60/120/300-second bounds and at most 1,000,000 captured
output bytes. Failed bounded output stays in private `.sealed-command-*.log`
files, never in normal status. Successful temporary input copies are removed.
Up to three interrupted `.sealed-bootstrap-<service>-*` stages are retained
per service; another attempt refuses until the operator inspects and explicitly
retires only those owned scratch directories. Do not remove intents, completed
environments, stable build evidence, credentials, journals or captures to force
a retry. No byte-identical virtual-environment or live-upgrade claim is made.

### Sealed bootstrap qualification

The existing managed-stack trial accepts `--dependency-plan <reference.json>`
and `--deny-bootstrap-network`. Its acquisition helper is an explicitly online
test fixture, separate from supported installer admission:

```sh
python tests/contracts/prepare_dependencies.py \
  --openjules-checkout /absolute/openjules \
  --polyphony-checkout /absolute/polyphony \
  --work-dir /absolute/new-python-artifacts
python tests/contracts/check_stack.py \
  --openjules-checkout /absolute/openjules \
  --polyphony-checkout /absolute/polyphony \
  --image sha256:<approved-local-trial-image-id> \
  --dependency-plan /absolute/new-python-artifacts/reference.json \
  --deny-bootstrap-network \
  --work-dir /absolute/new-sealed-stack-trial
```

Acquisition records exact artifacts for the current clean revisions and
interpreter. It uses the trial's declared pip/setuptools/wheel versions and
resolves the producers' requirements into a finite input set; it is not a
dependency update service or an already-reviewed production lock. Review the
resulting plan and artifacts before using them elsewhere.

The additional CI job uses the shared OpenJules and Polyphony pins above.
It calls OpenJules' own finite
image build and archive verifier with the committed reviewed input manifests,
imports that exact archive into the ordinary trial's Docker Engine, and passes
the verified immutable image ID to this same installed trial. The OpenJules
checkout includes the Git history needed for the separately pinned agent helpers
and production Dockerfile. ZPM does not resolve image packages or parse image
archives. The executable producer commands and exact manifest hashes are in the
[`sealed-managed-stack` workflow](../.github/workflows/ci.yml).

The producer build performs explicit online acquisition of declared inputs;
its Docker build disables networking. Archive verification checks the hash of
the record just created by that reviewed local build before import. Reusing an
archive from another origin requires separate approval of its record and hash.
The imported image then runs the existing controlled task/capture/drain/restart
scene. Image acquisition, native image qualification and installed bootstrap
remain distinct evidence boundaries.

This combined lane requires Linux/amd64, CPython 3.12, glibc 2.17 or later,
Git, a rootful Docker Engine with Docker CLI and `dockerd`, and the host's
existing sudo/unshare capability. These host tools are prerequisites. The
producer enforces its own acquisition, archive, storage and duration bounds;
the installer retains the independent plan and command bounds above. Reports
retain their separate measured scopes and do not claim a combined download,
storage or peak-memory total. The archive and private build directory remain
job-local. Only the producer's named safe metadata, exact dependency plan and
credential-free installed evidence are uploaded.

Bootstrap and repeat bootstrap run in a private network/PID namespace after
dropping back to the invoking UID;
the report retains namespace/network observations, elapsed time, scoped
waited-child peak RSS and separate artifact/installed-storage measurements.
Namespace setup requires the qualification host's existing sudo/unshare
capability, not extra runtime service privileges. Subsequent real loopback
service traffic uses the ordinary trial, which runs a protected task, captures
it, drains to zero, restarts and checks retained state. The ordinary ranged-mode
CI trials keep their existing image build paths. This evidence uses the imported
production managed image with deterministic execution; it establishes neither
live model behavior, external forge publication, two-host operation nor browser
support. Public binary distribution remains subject to OpenJules' separate
permission and notice requirements. Exact native run and revision results
belong in the delivery record after they pass.

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
