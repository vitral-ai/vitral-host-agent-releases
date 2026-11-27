# Install Vitral Connect on a Linux VM

Use the [tagged renderer](https://github.com/vitral-ai/vitral-host-agent-releases/tree/connect-vm-publish-v0.4.4/connect)
with the immutable [Connect VM v0.4.4 release](https://github.com/vitral-ai/vitral-host-agent-releases/releases/tag/connect-vm-publish-v0.4.4).
It bundles signed Vitral Host v0.4.4 and the CSL v0.5.7 workload collector.

| Architecture | Archive | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| x86-64 | `vitral-connect-linux-amd64.tar` | 12032000 | `f4d64f82e020e655b9f22c45c3ad03e9ec937ee6e0e846c8de4f909f82eaa454` |
| AArch64 | `vitral-connect-linux-arm64.tar` | 11304960 | `0c845e18d0609fb1d11aa38c445bc29624deba731b8f69a5f4a7aa0372752f63` |

The VM needs systemd, Python 3.11 or later, OpenSSH and an existing nonroot
account. Render its user data with `python3 connect/cloud_init.py --help`.
Set `--url` to the selected archive under
`https://github.com/vitral-ai/vitral-host-agent-releases/releases/download/connect-vm-publish-v0.4.4/`,
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
If the installed Vitral host supports `install-session-hook codex`, the
owner-scoped setup registers and authorizes that exact SessionStart hook.

Once enrollment succeeds, check the host and collector service status. Sign in
to your agent with your own account and start it in the host keeper.


The bootstrap enables systemd linger for the service account passed as
`--owner`, so its tmux panes can survive the last SSH logout.
