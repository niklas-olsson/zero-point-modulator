# Native Alpine attempt 5: capability inspection refused

Agent-authored by ChatGPT `/root/assess_polyphony`, 14 November 2026.
**FAILED; native installation and interfaces remain unqualified.** The run
passed artifact bytes, APK trust and base-image identity, then recorded
`CONTAINER_CAPABILITIES` in the assembly transaction stage. Owned cleanup is
`FAIL`, with `CONTAINER_MUTATION_OUTCOME_UNKNOWN`. The actual inspect payload
and precise failed call site were not retained. The source correction below
does not retroactively identify those missing observations or change the result.

## Exact run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43630719147, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43630719147) |
| Harness commit / tree | `a4565d9bef9327e132dc4e811732cc16cc922a61` / `724ab13e9d6cb6b63b115cf4d0bb70a9a75c425b` |
| Original active plan | [active-plan-5.json](../../../tests/contracts/native-alpine/active-plan-5.json), 7,591 bytes; SHA-256 `017c6ffb7c0a15b8efbc3046da53e3978d45f9b3bb8bda1d264d579f638575cf` |
| Source / native jobs | `132556466764` PASS / `132556566350` FAIL |
| Artifact | `14127713276`, 1,350-byte ZIP; SHA-256 `b7ef99678248df7963dcd131ec9d7431ff0c554d231b9d88700d1689f717ce4a` |
| Original closed summary | [summary.json](summary.json), 2,168 bytes; SHA-256 `b07450ad519b2c732344036f1d400f8c42f46cba1e84f0a93c1d46370e3c07fe` |
| Decoded native job log | 44,624 UTF-8 bytes including its initial byte-order mark; SHA-256 `92c5fad83b59993a3d5383bb54d3f53af94e10ca8b6acdcffd49a26e7fa9f0fe` |
| Derived binding | [run.json](run.json), coordinator metadata rather than a native receipt |

The investigator independently fetched the live run/job/artifact metadata, ZIP
and decoded log. The ZIP matches GitHub's advertised digest, has exactly one
bounded ordinary `summary.json` member, and matches the coordinator's independent
copy byte-for-byte. Its member and decoded log also match independently. The
native command exited 1; summary upload passed. The private Actions artifact
expires 28 November 2026. The exact closed summary remains here. No private
installation, raw inspect payload, acquisition binary or command log is added
to the repository.

## Actual result and limits

All 173 fixed artifacts completed: 178 requests, 209,315,064 observed body bytes,
228,403,438 reserved body bytes, 11,665,408 reserved metadata bytes, 178,405
response-header bytes and 13 token-framing bytes. The token and four permitted
OCI redirects passed. The measured body count includes the token response; the
fixed artifact catalog still totals 209,309,575 bytes. The acquisition used the
same five declared origins, with no retry or replacement catalog.

`artifact_bytes`, `apk_trust` and `base_image_identity` passed.
`apk_transaction` and `owned_cleanup` failed. Installed APK inventory,
interpreter identity, production prepare/verify, sealed bootstrap, native
imports/interfaces and repeat-byte checks remain `NOT_RUN`. The failed assembly
stage must not be presented as a working installation. The summary does not
retain the assembly container ID, inspect response, create acknowledgement or
guest phase history, so it cannot by itself establish their exact chronology.

The host reported Ubuntu image `20261111.370.1`, kernel `7.0.0-1017-azure`, Python
3.12.3, Git 2.56.1, OpenSSL 3.0.13, Docker client/server 28.0.4 and Engine API
1.45, UID/GID 1001. It recorded 21,750 ms before final writing, 141 resource
samples, a maximum sample gap of 278 ms, sampled peak host RSS 206,966,784 bytes,
peak logical owned storage 919,563,666 bytes and minimum host-available memory
15,408,676,864 bytes. Command output was 70,979 bytes. Container memory/PID/CPU
counters were zero. These are retained sampled counters, not instantaneous peaks
or proof that no intermediate native effect occurred.

Final pending mutation reservations were zero, but cleanup explicitly failed.
The existing cleanup policy keeps the uncertainty of a lost create reply even
after an exactly matched owned container is removed and both ID and name are
observed absent. Zero remaining accounting reservations cannot erase that
separate uncertainty. This policy is unchanged.

## Pinned source diagnosis

The source investigation pinned Docker CLI `v28.0.4` at
`b8034c0ed70494a90c133461d145cd072d920d7c` and Engine `v28.0.4` at
`6430e49a55babd9b8f4d08e70ecb2b68900770fe`. All ten retained primary file bytes
were independently checked against their upstream Git blob IDs; the complete
table is [docker-primary.json](docker-primary.json). The selected CLI's vendored
client has the same `container_create.go` blob as the selected Engine tree:
`9bb106f7763770fb896a510860bf842df1f73c33`.

| Primary source | Established behavior |
| --- | --- |
| [CLI option construction](https://github.com/docker/cli/blob/b8034c0ed70494a90c133461d145cd072d920d7c/cli/command/container/opts.go) and [create call](https://github.com/docker/cli/blob/b8034c0ed70494a90c133461d145cd072d920d7c/cli/command/container/create.go) | The fixed `--cap-add` values enter HostConfig and the CLI calls its vendored `ContainerCreate`. |
| [Vendored client normalization](https://github.com/docker/cli/blob/b8034c0ed70494a90c133461d145cd072d920d7c/vendor/github.com/docker/docker/client/container_create.go) | Before the create request, both capability lists are uppercased, non-`ALL` names gain `CAP_`, duplicates are removed and names are sorted. This path also applies to API 1.45. |
| [Engine create](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/create.go) and [HostConfig ownership](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/container.go) | The validated request HostConfig becomes the container's HostConfig. |
| [Engine inspection](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/inspect.go) and [API response](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/api/server/router/container/inspect.go) | Inspection copies that stored HostConfig. API 1.45's compatibility handling does not restore bare capability names. |
| [Engine capability interpretation](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/oci/caps/utils.go) | Drop `ALL` with the three additions selects only those additions. Canonical names express the same requested capabilities. |

The [executed harness source](https://github.com/niklas-olsson/zero-point-modulator/blob/a4565d9bef9327e132dc4e811732cc16cc922a61/tests/contracts/check_alpine_sealed_runtime.py)
instead compares inspect `CapAdd` with the bare CLI list
`["CHOWN", "DAC_OVERRIDE", "FOWNER"]`. A controlled fixture using the actual
`assert_container` and Docker's exact canonical list
`["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER"]` reproduced
`CONTAINER_CAPABILITIES` on that source. No Docker, acquisition or guest code ran
in the reproduction. This proves a source defect consistent with the hosted
failure. The reported binary version and source proof do not reconstruct the
unretained native payload or attest the installed binaries' build provenance.

The sampler can inspect a newly created container while `docker create` still
has a pending CLI reply. An observation failure can therefore interrupt that
reply before `mutation_confirmed` becomes true. The later cleanup can find only
the exact random owner/name/ID, remove it and prove absence, while still recording
`CONTAINER_MUTATION_OUTCOME_UNKNOWN`. This source path explains why those two
summary facts are compatible. It is not a claim that the missing hosted
ownership journal established every step of this chronology.

## Separate sixth source proposal

The [sixth plan](../../../tests/contracts/native-alpine/active-plan-6.json)
binds exact canonical inspect expectations. The CLI still requests the same
three assembly capabilities and drop `ALL`; qualification adds none. Observed
names are not normalized, sorted or deduplicated by the harness. Missing,
malformed, extra, duplicate, reordered or noncanonical values refuse. Empty
qualification additions require an explicit null or empty list, not a falsey
scalar. The actual create, pre-start check and ongoing sampler share this check.

Five new controlled cases cover the canonical response, all of those refusals,
an observed created container before the unchanged CLI acknowledgement, and a
lost acknowledgement that still leaves cleanup `FAIL` after owned absence.
The latter does not weaken or renew any cleanup deadline. The original
candidate, artifacts, production tuple, guest commands, requested authority,
resource limits, reservation policy and all five historical plans remain
unchanged. A new attempt/work/upload identity prevents reuse of the old result.
Source validation, independent review, fresh required gates and a separate exact
admission must precede any sixth native trial. None is activated by this record.
