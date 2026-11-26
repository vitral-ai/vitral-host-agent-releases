# Install Vitral Connect on a Linux VM

Use the files in this directory from the same immutable `connect-vm-publish-vX.Y.Z`
tag as the archive. Select `vitral-connect-linux-amd64.tar` for x86-64 or
`vitral-connect-linux-arm64.tar` for AArch64. Use the release asset's byte count
and the corresponding digest in `SHA256SUMS`.

The VM needs systemd, Python 3.11 or later, OpenSSH and an existing nonroot
account. Render its user data with `python3 connect/cloud_init.py --help`.
Set `--url` to the selected archive under
`https://github.com/vitral-ai/vitral-host-agent-releases/releases/download/<tag>/`,
and supply `--size` and `--sha256`. The installer verifies the tagged GitHub
Actions signer before checking and extracting the archive.

Authorize the OIDC rule or one-use join token in Machines and runtimes. Place
the credential on the VM through the provider's authenticated guest channel.
Use a root-owned regular file with mode 0400 or 0600, under root-controlled
directories, and give only its path to `--credential`. Supply the file before the
installation command runs. On a guest that boots before the credential arrives,
rerun the rendered installation command after the handoff.

Add `--install-agent` to install the pinned full package for the selected
agent. For Codex, the guest also needs `ps`, available in Ubuntu's `procps`
package. Codex retains the package resources used by its default daemon.

Once enrollment succeeds, check the host and collector service status. Sign in
to your agent with your own account and start it in the host keeper.
