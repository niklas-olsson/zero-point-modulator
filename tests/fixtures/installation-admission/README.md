# Installation-admission fixtures

These data fixtures support ZPM #56's pure comparison slice. No fixture is an
installed receipt, source attestation, task permission or execution result.

## OpenJules attribution

`openjules-installation-v1.json` is copied byte-for-byte from
[OpenJules revision 77e1191bc373c9a7bd999eaf33bc5a0b54d8323c](https://github.com/niklas-olsson/OpenJules/blob/77e1191bc373c9a7bd999eaf33bc5a0b54d8323c/src/worker/contracts/openjules-installation-v1.json),
delivered by PR #60. Original contributor and license attribution remain with
that public repository and revision. SHA-256 over exact bytes is
`1eaa70ae5ea2b28259c7f7f1884bb93335f7f51cd3dc1edbaf58710f9644573b`.

`openjules-vectors.json` retains six unchanged vector objects and their original
metadata from the adjacent producer `openjules-installation-v1-vectors.json`:
root, model, children, initial outcome, managed public and Boolean port refusal.
The containing JSON is reformatted and subsetted; it is not presented as the
original vector file's byte digest. Each original configuration string, expected
receipt and native fingerprint is unchanged. ZPM does not recompute policy
fingerprints or run producer code to consume these fixtures.

## Polyphony definition status

`polyphony-definition-vectors.json` is authored by ChatGPT for the coordinating
ZPM #56 workstream. It models the exact first result shape adopted in
[Polyphony #139 comment 6869132293](https://github.com/niklas-olsson/keystone-polyphony/issues/139#issuecomment-6869132293),
with separate root and child examples. Contract and profile digests are
deliberately synthetic repeated hexadecimal values. There is no claim that these
are emitted by native code or present in a released producer artifact.

The test's selected Polyphony revision records the inspected source context,
`d5f0cd14c1940e1bd55db1dc5d4569a5d14fb359`; it does not assert that #139 is
implemented there. Its missing native implementation/ensemble checkpoint and
paired installed acceptance remain independent dependencies. Replace definition
examples with attributed actual native output only when that producer is
delivered and qualified; never reinterpret these fixtures as historical native
evidence.

See the [canonical installation guide](../../../docs/single-host-installation.md#installation-admission-contract-fixtures)
for API, privacy, bounds and phase semantics.
