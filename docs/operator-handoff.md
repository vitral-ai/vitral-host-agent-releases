# Temporary operator-assisted release path

This public repository distributes approved host-agent binaries and signature
bundles. The private backend builds and signs them. This job never builds or
signs artifacts, creates a release/tag, uploads assets, mints tokens, changes
settings, or accesses private source. A dedicated App can replace the manual
staging step later; it is not required by this workflow.

## Preconditions and review

The coordinator owns the final qualified-publish gate. Before any real staging,
obtain the exact qualified producer commit/tag/workflow and final G1 receipt,
both architecture binaries and their original private-producer Sigstore bundles,
and the five final hashes/sizes. Review these facts before staging. The real draft
ID collected during staging is then included in the final authorization described
in [`release-authorizations/README.md`](../release-authorizations/README.md). Do not
stage test payloads or create dummy immutable tags. Do not upload source code,
private receipt contents, logs, workflow copies, credentials, or an archive of a
private checkout.

The distribution tag targets a reviewed **public** repository commit (the initial
README commit is `a53272a082b09daea89306bbc588274f50059ab0`), not the private
producer commit. The signed certificate must independently match `source_sha`.
An authorization can be reviewed later than its chosen public target; this
avoids a self-referential authorization commit hash.

The current consumer source reference is
`vitral-ai/vitral_infrastructure@f251e8e2f6443d2460a35cdab5d875496bbc600e`,
`docs/host_agent_release_contract.md` and `runtime/host_agent_release.py`.
It accepts the public release download origin and the two binary/bundle pairs,
with exact identity/issuer pins. `SHA256SUMS` adds an operator audit manifest;
it does not replace any consumer pin or Sigstore verification. The checked-in
producer facts must be reconciled with the coordinator's final receipt before
creating the first authorization. This draft does not modify private workflows.

## Staging outside CI

Use the existing authorized `gh` session outside CI. Never copy its token,
authentication files, cookies, or login material into the repository or job.
The job uses `${{ github.token }}` scoped by GitHub to this repository, with
explicit `contents: write` only. It does not request `id-token: write`, use a
repository secret or pass credentials to Cosign.

One named operator owns one draft's staging-through-publish window exclusively;
other operators and automation must leave this tag/draft alone until readback
finishes. The workflow serializes runs of the same tag but cannot lock an
external operator. This is a bounded per-release ownership requirement.

After the workflow source and intended artifact/source facts are reviewed, use the
approved tag and full public target SHA. The following
are **operator instructions, not commands executed during source preparation**.
Set `RELEASE_TAG` and `PUBLIC_TARGET_SHA` to those reviewed values. From the
directory containing only the five final approved files:

```sh
gh api --method POST repos/vitral-ai/vitral-host-agent-releases/git/refs \
  -f ref="refs/tags/$RELEASE_TAG" -f sha="$PUBLIC_TARGET_SHA"
gh release create "$RELEASE_TAG" --repo vitral-ai/vitral-host-agent-releases \
  --draft --verify-tag --title "$RELEASE_TAG" \
  --notes "Verified host-agent binaries for $RELEASE_TAG."
```

For a prerelease version, add `--prerelease` to creation; for a stable version,
leave it absent. Use a lightweight tag; annotated tags are rejected. An existing
tag must match the approved target; never force or reuse a published version.
Upload by exact filename with no `--clobber`, shell wildcard or source archive:

```sh
gh release upload "$RELEASE_TAG" --repo vitral-ai/vitral-host-agent-releases \
  vitral-host-agent-linux-amd64 vitral-host-agent-linux-amd64.sigstore.json \
  vitral-host-agent-linux-arm64 vitral-host-agent-linux-arm64.sigstore.json \
  SHA256SUMS
```

Collect the actual draft release ID using authenticated List releases (which
includes drafts for callers with push access):

```sh
gh api repos/vitral-ai/vitral-host-agent-releases/releases --paginate \
  --jq '.[] | select(.draft == true) | {id, tag_name}'
```

Identify the single draft with exactly the approved tag, record its real numeric
`id`, and inspect it using `GET /repos/vitral-ai/vitral-host-agent-releases/releases/{id}`.
Now prepare, review and merge the final authorization including this `release_id`
and final five hashes/sizes. Never invent a release ID or dummy draft to fill the
schema. The verifier uses this exact numeric ID before and after publishing and
on retries; it never uses the published-release-by-tag endpoint to retrieve a
draft. A returned ID or tag mismatch fails before downloads or writes.

Immediately before dispatch, using existing `gh` **outside CI**, require
`enabled: true` from:

```sh
gh api repos/vitral-ai/vitral-host-agent-releases/immutable-releases
date -u +%Y-%m-%dT%H:%M:%SZ
```

Record that nonsecret result and its fresh UTC timestamp in the private receipt.
Dispatch the main-branch workflow with `tag`, this `immutable_preflight_utc` and
`exclusive_staging_owner=true`. The timestamp is an operator assertion, accepted
only within 15 minutes; it is not independent API proof of the setting. If the
run queues too long, repeat the outside-CI read and dispatch with a fresh time.
Do not bypass required source review/checks to add an authorization or workflow.

## Verification, publishing and limits

The verifier checks public repository ID/name, approved real release ID, exact
lightweight public tag target, fixed release metadata, five unique uploaded asset IDs/names/sizes/API
digests and all downloaded SHA256s. It verifies the four-line checksum manifest
and both private producer signatures. It rereads release/tag/asset metadata
immediately before its only possible write: `PATCH` that release with
`{"draft": false}`. It does not retry this write on a failed/uncertain response.

The job then requires `draft: false` and `immutable: true`, the same release ID,
tag target and asset fingerprints, and downloads and verifies all five published
files and both signatures again. Only then does it print `verified_immutable`.
A rerun of an already immutable release performs only verification/readback.
Download redirects accept only HTTPS `release-assets.githubusercontent.com`;
authorization headers are not forwarded to that host. Downloads are bounded.
Staged binaries are never executed. The SHA-pinned Cosign subprocess receives a
fresh temporary HOME and only PATH/HOME/TMPDIR environment values.

Two platform limits remain explicit:

1. GitHub's immutable-setting read requires Administration:read. The repository
   `GITHUB_TOKEN` with contents:write cannot perform it. This path relies on the
   operator's fresh preflight and detects false/unknown immutable state after
   publishing. It cannot guarantee immutability was enabled at the publish
   instant if an administrator changes settings during the window.
2. GitHub documents no atomic asset-digest compare-and-publish operation. There
   is a race between the last metadata read and publishing. Exclusive ownership
   narrows that window; post-publish immutable byte verification detects a
   mismatch but cannot undo a release. This source does not claim atomic CAS.

If either risk is unacceptable for a particular release, do not dispatch it.
Do not broaden the job credential or substitute a public signing identity.

## Failure, reconciliation and reversal

Before publishing, a failed check leaves the draft untouched. Correct the real
producer/output/authorization through review, then restage under the coordinator's
direction. This workflow performs no destructive cleanup.

`seal_outcome_unknown_readback_required` means the PATCH may have succeeded.
Read the release by its authorized numeric ID and inspect its immutable/draft
state before any further action. An ordinary rerun only verifies an already immutable release. False,
missing or unknown immutability, changed bytes, identity failure, or absent
readback never counts as success. Preserve the nonsecret run/release IDs and
verification result for the coordinator. A published mismatch requires a new
version and operator incident review, not an attempted overwrite, forced tag,
or deletion. Immutable release assets and tags cannot be replaced.

To withdraw this temporary automation, remove/disable the workflow through the
normal reviewed repository change and withhold new authorization records. Each
run's GitHub token expires automatically; no durable publisher credential was
introduced. A staged unpublished draft/tag remains for the coordinator to
inspect; deleting it is not part of this preparation. Do not disable immutable
releases to repair a published version.

## Local validation and reference pins

Run `python3 -m unittest discover -s tests -v`. Fixtures are synthetic local data
created in memory/temp directories; tests never use GitHub credentials, network,
live drafts, tags, or artifacts. They cover authorization rejection, finite asset
checks, digest/signature failures before writes, staging races, single uncertain
write/no retry, immutable readback, rerun behavior, exact private identity/source
flags, credential-free verifier environment and redirect credential stripping.
They do not prove that a real producer signature passes or that a real release
is qualified. The actual coordinator gate supplies those facts.

- Checkout: `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683` (v4.2.2).
- Cosign: v3.1.4 linux-amd64, SHA256
  `424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa`;
  matches the pinned consumer tool digest.
- [GITHUB_TOKEN scope and permissions](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token).
- [Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).
- [Immutable-setting API and required Administration permission](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository).
- [Release APIs](https://docs.github.com/en/rest/releases/releases).
- [Cosign v3.1.4 certificate flags](https://github.com/sigstore/cosign/blob/v3.1.4/cmd/cosign/cli/options/certificate.go).
