# Vitral host client draft signing

The publisher stages exactly the six client binaries and `SHA256SUMS` as a
draft. Once all seven assets are uploaded, push the matching tag on a reviewed
commit of this distribution repository that contains the signer. A rerun may be
dispatched against the tag once the workflow is on the default branch.
The workflow checks all six binaries against the manifest, signs the manifest
with the public tag's GitHub Actions identity, verifies the bundle and attaches
it to the draft.

## Staging dry run

Use `host-agent-publish-v0.0.0-dryrun.N` on the producer and distribution
repositories. These tags select the Sigstore staging TUF root and services.
Leave the release in draft. For the distribution step, after the publisher job
has succeeded:

```bash
tag=host-agent-publish-v0.0.0-dryrun.1
git tag "$tag" <reviewed-distribution-commit>
git push origin "refs/tags/$tag"
gh run list --workflow sign-host-release.yml --branch "$tag"
```

Use the signing job's draft release ID to download the eight assets with
authenticated GitHub access. On a clean Linux VM with Cosign v3 installed, run:

```bash
./install.sh --tag "$tag" --assets-dir ./draft --bin-dir ./verified-bin
./verified-bin/vitral-host version
```

Alter a byte in the selected architecture's binary and run the installer
against that copy to check the checksum failure. Pass an identity for another
workflow to `cosign verify-blob` with the same bundle and the staging trusted
root to check identity rejection. Remove the bundle from a disposable
test copy and check that the installer refuses to install.

Production signing requires the explicit
`VITRAL_PRODUCTION_SIGNING_ENABLED=true` repository setting. The first real
publication needs separate owner approval.
