# Vitral host client releases

The public release includes Linux, macOS, and Windows clients for amd64 and
arm64, plus a SHA256 manifest and its Sigstore bundle. You need Cosign v3 on
your PATH. All downloads are public.

## Linux and macOS

```bash
tag=host-agent-publish-v0.4.2
curl -fsSLo install.sh "https://raw.githubusercontent.com/vitral-ai/vitral-host-agent-releases/$tag/install.sh"
bash install.sh --tag "$tag"
"$HOME/.local/bin/vitral-host" version
"$HOME/.local/bin/vitral-host" install
```

## Windows 11

Run these commands as the user who will run the host, from PowerShell:

```powershell
$tag = 'host-agent-publish-v0.4.2'
curl.exe -fsSLo install.ps1 "https://raw.githubusercontent.com/vitral-ai/vitral-host-agent-releases/$tag/install.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Tag $tag
$hostExe = Join-Path $env:LOCALAPPDATA 'Vitral\bin\vitral-host.exe'
& $hostExe version
& $hostExe install
```

The Windows command applies its execution policy to the child PowerShell process only.

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

## Guided host and Sessions setup

Run the guided scripts from a verified checkout of [bootstrap commit `e657a888438d0460dee3e5ee180ac9e038276a99`](https://github.com/vitral-ai/vitral-host-agent-releases/tree/e657a888438d0460dee3e5ee180ac9e038276a99). Keep the scripts, installer, and dependency pins together from that checkout. Pass the signed host tag `host-agent-publish-v0.4.2` shown above to install the host binary.
Your Vitral administrator supplies the four deployment values.

```bash
./setup.sh --tag "$tag" --api "$api" --registry "$registry" \
  --discovery-url "$discovery" --client-id "$client_id"
```

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Tag $tag -Api $api -Registry $registry -DiscoveryUrl $discovery -ClientId $clientId
```

The scripts verify dependencies before extracting them and use the existing
Sigstore release installer for the host executable. They install under your
user account. Follow the host device approval and collector browser sign-in,
then choose which sessions to upload. The Windows script continues to rely on
the existing signed host release format.

To link a keeper to its exact Codex or Claude Code conversation, follow the [agent hook setup](docs/session-hooks.md).

### Configuración guiada en español

Ejecute el script de su sistema desde la carpeta de instalación verificada.
Use los valores de API, registro, descubrimiento e ID de cliente que le
entregó su equipo. En Linux o macOS ejecute `setup.sh`. En Windows abra
PowerShell con su cuenta habitual y use el comando de Windows de arriba.
El comando aplica la directiva de ejecución solo al proceso que ejecuta el instalador.

Complete la aprobación del host y el inicio de sesión del colector cuando
el asistente se lo indique. Elija solo conversaciones nuevas, las últimas N,
todas o las de una carpeta. Solo las nuevas es la opción predeterminada.
Al finalizar, use `vitral-host doctor` para comprobar la conexión y el
colector. Los enlaces exactos a Codex y Claude Code son opcionales y se
configuran con la [guía de ganchos](docs/session-hooks.md).
