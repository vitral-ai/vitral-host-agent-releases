# Reviewed release authorization

There is deliberately no active authorization in this repository preparation.
An absent authorization fails before network access or any write. Never add a
sample release or placeholder authorization to activate the workflow.

For each actual qualified release, the coordinator reviews a new JSON file named
`host-agent-v<version>.json` in this directory through the normal source review
process. Use only the fields below; unknown fields, duplicate keys, missing or
extra assets, invalid SemVer, and invalid sizes/digests fail closed.

| Field | Required value |
| --- | --- |
| `schema_version` | Integer `1` |
| `repository` | `vitral-ai/vitral-host-agent-releases` |
| `repository_id` | Integer `1463746626` |
| `tag` | Exact approved `host-agent-v` + SemVer |
| `public_target_sha` | Full lowercase 40-character commit SHA already in this public repository, selected for the distribution tag |
| `source_sha` | Full lowercase 40-character qualified private backend producer commit |
| `qualified_receipt_sha256` | SHA256 of the coordinator's final qualified-source/G1 approval receipt; the receipt remains private |
| `assets` | Object with exactly the five entries below |

Each asset entry has exactly `size` (positive integer byte count) and `sha256`
(lowercase 64-character SHA256). Exact names and maximum sizes:

| Name | Maximum bytes |
| --- | ---: |
| `vitral-host-agent-linux-amd64` | 134217728 |
| `vitral-host-agent-linux-amd64.sigstore.json` | 1048576 |
| `vitral-host-agent-linux-arm64` | 134217728 |
| `vitral-host-agent-linux-arm64.sigstore.json` | 1048576 |
| `SHA256SUMS` | 8192 |

`SHA256SUMS` contains exactly four lines sorted by filename, one for each binary
and signature bundle: lowercase SHA256, two ASCII spaces, filename, LF. Its own
digest/size is separately authorized in the JSON. No self checksum.

Signing policy is fixed in verifier source, never supplied as an input:

- Repository `vitral-ai/vitralai`; workflow `.github/workflows/host-agent-release.yml`.
- Exact certificate identity `https://github.com/vitral-ai/vitralai/.github/workflows/host-agent-release.yml@refs/tags/<approved-tag>`.
- Exact OIDC issuer `https://token.actions.githubusercontent.com`.
- Certificate GitHub repository, tag ref, source SHA and `push` trigger all match.
- Cosign v3.1.4 with pinned amd64 executable SHA256; default certificate,
  transparency-log and SCT checks remain enabled. No regexp identity or bypass.

The coordinator must first confirm the actual producer uses this contract and
that G1 qualification covers the exact signed bytes. This preparation does not
assert that a producer workflow exists or that any source has qualified. A
receipt digest records reviewed approval; the public verifier cannot evaluate
private G1 evidence. The review supplies that trust decision.
