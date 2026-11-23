# Vitral host client releases

The public release includes Linux, macOS, and Windows clients for amd64 and
arm64, plus a SHA256 manifest and its Sigstore bundle. You need Cosign v3 on
your PATH. All downloads are public.

## Linux and macOS

```bash
tag=host-agent-publish-v0.2.0
curl -fsSLo install.sh "https://raw.githubusercontent.com/vitral-ai/vitral-host-agent-releases/$tag/install.sh"
bash install.sh --tag "$tag"
"$HOME/.local/bin/vitral-host" version
"$HOME/.local/bin/vitral-host" install
```

## Windows 11

Run these commands as the user who will run the host, from PowerShell:

```powershell
$tag = 'host-agent-publish-v0.2.0'
curl.exe -fsSLo install.ps1 "https://raw.githubusercontent.com/vitral-ai/vitral-host-agent-releases/$tag/install.ps1"
& .\install.ps1 -Tag $tag
$hostExe = Join-Path $env:LOCALAPPDATA 'Vitral\bin\vitral-host.exe'
& $hostExe version
& $hostExe install
```

Both installers verify the checksum manifest against the exact release tag's
GitHub Actions signing identity, then verify the matching executable before
copying it to your user directory. The host's `install` command opens the
device approval flow and configures the per-user host service.

For manual Sigstore verification, download `SHA256SUMS` and
`SHA256SUMS.sigstore.json` from the release, then use the same exact tag:

```bash
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "https://github.com/vitral-ai/vitral-host-agent-releases/.github/workflows/sign-host-release.yml@refs/tags/$tag" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
```

Compare the downloaded executable's SHA256 to its line in the verified
manifest. Use `sha256sum` on Linux, `shasum -a 256` on macOS, or
`Get-FileHash -Algorithm SHA256` on Windows.

The signing workflow attaches the bundle to a draft. Publication requires a
separate operator decision. See the [draft signing procedure](docs/client-draft-signing.md).
