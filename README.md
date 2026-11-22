# Vitral host client releases

Vitral host clients use tags `host-agent-publish-v<semver>`. Each release
includes Linux amd64 and arm64 binaries, `SHA256SUMS`, and
`SHA256SUMS.sigstore.json`.

Download `install.sh` from the release repository at the tag you want. With
Cosign v3 installed, run:

```bash
./install.sh --tag host-agent-publish-v<VERSION>
vitral-host version
```

The installer verifies the signed checksums before installing the executable
in `~/.local/bin`. Run `vitral-host install` to enroll and start the host
client. For manual verification, use the exact release tag:

```bash
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "https://github.com/vitral-ai/vitral-host-agent-releases/.github/workflows/sign-host-release.yml@refs/tags/$tag" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
sha256sum --check --strict SHA256SUMS
```

The signing workflow attaches the bundle to a draft. Publication requires a
separate operator decision. The [draft signing procedure](docs/client-draft-signing.md)
covers staging tests and updated release verification. Earlier
`host-agent-v<semver>` releases use their historical verification procedure.
