# Install separate controller and execution roles

Deployment schema 3 prepares role-local bundles for the full destination
contract delivered by [ZPM #50](https://github.com/niklas-olsson/zero-point-modulator/issues/50).
It reuses the existing installer, dependency bootstrap, capacity journal and
native service entry points. The bounded generator and installed one-host trial
are part of [#51](https://github.com/niklas-olsson/zero-point-modulator/issues/51);
that issue also requires a later acceptance run on two actual execution hosts.

| Role | Required sources and installed environments | Local state and authority |
| --- | --- | --- |
| `controller-authority` | ZPM and Keystone Polyphony | ZPM enrollment, approvals and capacity; canonical task policy, attempts, acceptance and artifacts in Polyphony |
| `execution` | ZPM manager and OpenJules | Original destination identity, host admission, runtime execution, recovery and capture |

The controller role needs no OpenJules source, task checkout, approved image or
Docker access. An execution bundle contains no Polyphony source/environment,
authority database, ZPM capacity journal or owner/reviewer/ingress credentials.
Its ZPM environment runs the installation manager, not another reconciler.
One controller journal remains the sole capacity authority for this deployment.
Destination placement does not assign canonical tasks or accept their results.

## Supported inputs and locations

Use Linux, Git, Python 3.11 or 3.12, private local storage, and the existing
[atomic preparation requirements](single-host-installation.md#supported-host-and-execution-boundary).
Each execution host also needs its authorized local Docker Engine, a clean task
checkout at the approved base, and the exact approved image already installed.
No image is pulled by preparation or initialization. Each service listens on
literal loopback. Operators supply the HTTPS frontends and network routes for
remote access; the generator does not deploy a proxy or change host security.

ZPM's lifecycle transport supports verified HTTPS, including an explicitly
configured `SSL_CERT_FILE`/`SSL_CERT_DIR`. OpenJules' canonical-authority client
uses its installed trust store and does not take a private authority CA from
those ZPM transport variables. A remote authority therefore needs a certificate
trusted by that exact installed client. Local qualification may explicitly use
literal `127.0.0.1` or `::1` HTTP. An authority URL must be at the URL root;
lifecycle enrollment can use its separately supported endpoint path.

Execution and Docker workspaces are on the selected execution host. Runtime
journals and captures persist in that bundle's `state/openjules/`. ZPM capacity
and approved public contracts persist in the controller's `state/zpm.sqlite3`;
canonical task records and accepted artifact storage are in its
`state/polyphony/`. This first role schema configures deterministic root commands,
with no model facility, child delegation, forge publication or browser runtime.
There is no inference location to qualify in this path because model calls are
absent. The public application demonstrator and its browser-local model path
remain separate acceptance work.

## Prepare the role configurations

Start from [the controller example](../examples/deployment-controller.json) and
[the execution example](../examples/deployment-execution.json). Replace every
placeholder with an actual approved path, revision, image or command. For a
second execution bundle, change its destination ID, runtime principal and both
credential references to the second declared pair. On one host it also needs
a distinct port; independent hosts may use the same port.

Every role shares the exact deployment ID, canonical repository, profile and
approved source base. The controller requires only that base's immutable
40-character Git identity, not the source checkout. Runtime profiles are limited
to the documented native character set and whole-second durations expressed as
integer milliseconds. The initial host ceiling is an integer from zero through
`2**53 - 1`. These parser bounds are not evidence that a large configuration has
been exercised. Size host resources and ceilings for the actual workload.

The role source sets are closed. Controller configuration requires exactly
`zpm` and `polyphony`; execution configuration requires exactly `zpm` and
`openjules`. Schema 1 and 2 retain their existing three-service behavior.
Targets must be zero at fresh role preparation. Raising capacity later changes
the retained journal, not the sealed preparation configuration.

### Credential custody

Before first preparation, supply the declared `env:ZPM_INSTALL_...` values
through the operator's private secret mechanism. References are explicit and
distinct; values must be distinct non-whitespace tokens of 32 to 4096 characters.
Preparation does not invent a cross-host delivery channel or print these values.

| Secret | Controller authority | Matching execution role | Other execution roles |
| --- | --- | --- | --- |
| Runtime A credential | Scoped runtime principal A | Outgoing authority client | Absent |
| Lifecycle A credential | Outgoing destination transport A | Incoming scoped lifecycle API | Absent |
| Runtime B / lifecycle B | Corresponding separate entries | Only on execution B | Absent elsewhere |
| Owner, reviewer and ingress | Generated and retained locally | Absent | Absent |

An execution runtime credential identifies its own principal. The lifecycle
credential is scoped to this deployment/repository. Each controller catalog
entry binds one destination ID to its exact token reference; importing another
host's reference is refused even if that reference also exists in the catalog.
The outgoing child environment is constructed from the relevant private map;
unrelated host provider/owner credentials are not inherited.

All generated credentials remain private files, not command-line arguments.
Repeating preparation with the same normalized inputs reuses the original
values without resolving the operator environment again. It preserves current
targets, activation, enrollment, admission and original identities. Adding hosts,
changing source pins/profile or rotating credentials through re-preparation is
not a supported update operation.

Run on each intended host with its role-local input:

```sh
zpm deployment prepare /absolute/private/role.json \
  --directory /absolute/private/role-bundle
zpm deployment verify /absolute/private/role-bundle
cd /absolute/private/role-bundle
python3 manage.py bootstrap
```

`prepare` and `verify` perform bounded local data/Git reads and private bundle
writes. They neither execute application code nor initialize a destination.
`bootstrap` installs only the role's two isolated Python environments. Ordinary
bootstrap retains the existing ranged dependency behavior. Bootstrap itself
does not start services, approve a contract, open admission, enable a destination
or raise a target.

### Sealed dependency plans

The optional `dependency_plan` reference has the same shape and artifact custody
as [sealed local Python artifacts](single-host-installation.md#sealed-local-python-artifacts).
Role installation requires a plan with `schema_version: 2`, exactly the selected
role's source/environment set, and exactly the union of its referenced artifacts.
The controller plan contains `zpm` and `polyphony`; execution contains `zpm` and
`openjules`. Extra environments, unused wheels, missing sources and a version-1
plan are refused. Legacy deployment schemas still require the original version-1
plan with all three services. This plan version is separate from deployment,
producer configuration and wire versions.

Acquire and review wheels separately. A role plan can be derived from an approved
three-service plan by retaining the selected two source/environment records and
their artifact union, setting the plan version to 2, and recomputing its exact
reference digest. This is data preparation, not a resolver or acquisition step.
The native trial records whether bootstrap actually ran with IP networking
denied. Merely selecting a sealed plan does not prove that denial.

The OpenJules managed-interpreter selector remains version 1 and binds the exact
generated native configuration bytes, including native configuration schema 2.
The separate static `openjules-installation-v1` validator is still schema-1-only.
Its refusal of configuration schema 2 is expected; it is not relabeled as a
successful full-destination installation check.

## Explicit first-start sequence

Use separate terminals for foreground managers. Keep all capacity targets zero
and both destination entries disabled until the final activation step below.

### 1. Start the canonical authority alone

In the controller bundle:

```sh
python3 manage.py start --authority-only
```

First startup durably records its intent, starts only Polyphony, then checks the
exact scoped managed capability response while the owned process remains alive.
The manager retains a private file-identity receipt for the resulting authority
database. It starts no capacity reconciler in this mode. A later ordinary start
uses the same authority journal and starts the one existing ZPM watch loop.

### 2. Initialize and approve each stopped execution destination

In each execution bundle, with its own service stopped:

```sh
python3 manage.py initialize-destination
python3 manage.py preview-execution-contract \
  --output /absolute/private/new-host-approval.json
```

Review the complete public envelope, including scope, original destination
binding B, portable manifest and digest H. Preview retains the producer's exact
candidate but grants no approval. After the host operator approves that exact
document:

```sh
python3 manage.py approve-execution-contract \
  /absolute/private/new-host-approval.json
python3 manage.py start
```

The producer starts host admission closed. A valid initialized destination can
report `journals_initialized: false` before first service startup; it must retain
the same destination ID, journal incarnation and policy binding when it becomes
ready. Only the producer performs this transition. Export the exact public
approval through the operator's chosen channel after host approval. Neither
private journals nor runtime/host credentials accompany it.

### 3. Import original identities and approvals while placement is disabled

Build a private registry document using the original bindings returned by each
host and the exact declared lifecycle references. For example:

```json
{
  "schema_version": 2,
  "max_workers": 2,
  "timeout_seconds": 10,
  "destinations": [
    {
      "destination_id": "host-a",
      "journal_incarnation": "REPLACE_WITH_ORIGINAL_HOST_A_INCARNATION",
      "endpoint": "https://execution-a.example.invalid",
      "token_ref": "env:ZPM_INSTALL_LIFECYCLE_A",
      "max_workers": 1,
      "enabled": false
    }
  ],
  "profiles": [
    {
      "destination_id": "host-a",
      "repository": "github:github.com/owner/project",
      "profile": "approved-root-v1",
      "policy_binding_id": "REPLACE_WITH_ORIGINAL_HOST_A_POLICY_BINDING",
      "policy_binding_version": 1
    }
  ]
}
```

Add host B with its own retained values. In a second controller terminal:

```sh
python3 manage.py registry-import /absolute/private/disabled-registry.json
python3 manage.py import-approval /absolute/private/host-a-approval.json
python3 manage.py import-approval /absolute/private/host-b-approval.json
python3 manage.py activate --offline
python3 manage.py doctor --max-actions 16
```

`activate --offline` changes the controller's journal format while every writer
and reconciler is stopped, except the separately running authority service.
It does not enable destinations or raise the target. Fresh preparation already
retains a zero-work v2 offline journal; activation adds the existing full-mode
extension. Before explicit activation, `doctor` reports that remote qualification
has not run. It does not pretend an unactivated registry was checked.

With host admission closed, a complete authenticated check can report
`qualification_complete: true` and `ready: false`, exiting with status 3.
Inspect the retained details. Authentication, TLS, scope, B/H, policy or service
failures are distinct from this deliberate admission refusal. A complete
destination check consumes two I/O credits per destination; choose a budget that
checks the whole intended set in this observation.

### 4. Open host admission, then enable placement explicitly

With target and reserved capacity both zero and both host admissions confirmed
closed, import the same registry with chosen entries explicitly `enabled: true`:

```sh
python3 manage.py registry-import /absolute/private/enabled-registry.json
python3 manage.py status
```

Then stop the authority-only manager normally and wait for its owned service to
exit. Run `python3 manage.py start` in the controller bundle to start the ordinary
authority plus sole reconciler at target zero. This bootstrap transition creates
no worker reservations and preserves the original authority database.

Each execution host operator runs:

```sh
python3 manage.py open-admission
python3 manage.py inspect-admission
python3 manage.py doctor
```

These owner-local admission wrappers remain usable while that role's foreground
manager holds its lifetime lock. Initialization, preview and approval remain
stopped-service operations. The controller cannot open a remote host's admission.

After host checks confirm current admission, request capacity in another
controller terminal:

```sh
python3 manage.py target 2
python3 manage.py status
```

The watch loop reconciles this retained intent and holds the controller lock for
its lifetime. Controller `doctor`, registry/approval imports, format activation
and one-shot `reconcile` therefore require the watch to be stopped. In the
explicit authority-only mode, `python3 manage.py reconcile --max-actions 16`
can deliberately run that same controller once. `target` and local `status`
remain available during the ordinary watch. Use the supported canonical
ingress/review interfaces for task requests and acceptance; these installation
commands do not add task authority.

## Recovery and retirement

To retire work while the ordinary manager runs, set the controller target to
zero and let its watch reconcile until original receipts permit release. For
registry retirement, stop the watch before importing retained destination
records with `enabled: false`. For a planned manager-mode change, first drain to
zero retained reservations and confirm host admission closed. Imports are
additive: omitting an old entry does not disable it. Host
`close-admission` prevents new effects but does not cancel tasks, release unknown
capacity or prevent original observation/capture. The original B/H/A, launch key
and control keys remain in their producer and consumer journals. No failed
endpoint or changed identity authorizes silent relocation.

Repeat `verify`, `bootstrap` and `start` with the same bundle and pins for a normal
restart. Completed environments, private credentials and operator changes stay
retained. The execution manager records initialization intent before invoking
the producer. After a lost reply it can only inspect and bind the retained
original producer identity. It never retries a fresh initializer against absent
or unreadable original state. Once the ready transition has been observed, a
later initialized-only report is a refusal, not a new ready destination.
Approval attempts retain their exact private input before calling the producer;
the accepted-document receipt is written only after an exact acknowledged
success. Rejected input does not become approval. After a lost acknowledgement,
retry the original input through the producer's idempotent approval operation.

The controller retains first-start intent and the authority database's device
and inode plus a nonempty SQLite header check. It refuses a missing, replaced or
truncated database before another launch. An interrupted first start without a
confirmed receipt remains unavailable, even if a database happens to exist.
This is same-installation missing/replaced-file protection. It does not detect
rollback, authorize copied/restored databases or define a new canonical database
incarnation contract. Keep an unavailable bundle for diagnosis instead of
deleting its evidence or forcing initialization.

The fresh controller preparation uses the supported empty-v1 enrollment and
offline-v2 migration inside its unpublished staging directory. Its private
`state/prepared-v1.sqlite3` is a non-operative preparation checkpoint. It is not
a fallback journal and has no supported restore path after full activation.
Missing live state refuses even if that checkpoint survives.

## Qualification and remaining host acceptance

The selected component set is OpenJules
`7135d0988dff050df63acb89f75562dafccacf79`, Keystone Polyphony
`6480e36355bf0d5de4e25a560d40e665fd76038c`, and the exact ZPM revision tested by
the workflow. The earlier [#50 actual-main run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43611590617)
qualifies its installed full destination consumer; it does not qualify this new
role generator by itself.

The role unit suite checks closed role sets, secret confinement, exact native
configuration inputs, plan versions, format activation, live admission locks,
retained initialization, missing-state refusal and same-configuration restart.
The companion `tests/contracts/check_role_stack.py` exercises three separately
prepared and installed bundles in the ordinary and sealed managed-stack lanes.
Its bounded report must state the actual source pins, bootstrap mode, one-host
topology, scopes, portable manifests, installed command paths and remaining
limits. A pending or fixture-only result is not installed qualification.

The installed trial uses one Linux host and one Docker Engine, lifecycle HTTPS
with its declared test CA, and explicit loopback HTTP to the canonical authority.
It checks distinct task execution/capture, original-key recovery while closed,
retained refusal and capacity, credential/certificate/identity refusals, and
normal same-bundle restart through the generated managers. Model calls, child
delegations, forge actions and browser sessions are zero. Ordinary and sealed
bootstrap results are recorded separately.

Full #51 acceptance still needs named independent execution hosts, their
operator-provisioned scoped credentials and resources, trusted remote authority
TLS, actual lifecycle routes, and a recorded finite trial using the same approved
version set. Retain those operator details privately. Record host separation,
actual execution/persistence locations, original identities and approvals,
outage/recovery and retained results before completing that issue. The one-host
generator trial does not establish that outcome or a portable browser demonstrator.
