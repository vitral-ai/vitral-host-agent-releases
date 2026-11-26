# Connect VM draft signing

Connect VM bundles use a separate public release tag,
`connect-vm-publish-v<semver>`, in `vitral-ai/vitral-host-agent-releases`.
Stage exactly these three files in one draft with that tag.

- `vitral-connect-linux-amd64.tar`
- `vitral-connect-linux-arm64.tar`
- `SHA256SUMS`

Each archive must have a nonzero size no greater than 134,217,728 bytes.
The manifest has exactly two lines in the archive order above, each with a
lowercase SHA-256, two spaces, the filename, and a newline. Review the archive
provenance and draft digests before triggering signing. Fetch the separately
reviewed renderer from the exact public release tag before using it to verify
the signed archives.

Push the matching tag at the reviewed public distribution commit. The
`sign-connect-vm-release.yml` job checks the exact repository, tag, workflow,
draft asset set, and all digests. It signs the manifest with keyless Sigstore,
verifies the bundle, and attaches `SHA256SUMS.sigstore.json` to the draft. A
rerun can be dispatched against the same tag once the workflow is on the
default branch.

Tags of the form `connect-vm-publish-v0.0.0-dryrun.N` use Sigstore staging.
Leave staging releases in draft. Production signing uses
`VITRAL_CONNECT_VM_PRODUCTION_SIGNING_ENABLED=true`. Enable it only for the
reviewed production release and restore it to false after signing.

Verify the bundle against the production trusted root, the issuer
`https://token.actions.githubusercontent.com`, and the exact identity
`https://github.com/vitral-ai/vitral-host-agent-releases/.github/workflows/sign-connect-vm-release.yml@refs/tags/<tag>`.
Compare the signed manifest and the draft assets with the independently
reviewed digests. Publish under the authorized release account with GitHub
release immutability enabled, then read back the immutable release and digests.
