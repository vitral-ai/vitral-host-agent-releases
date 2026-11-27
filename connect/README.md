# Install Vitral Connect on a Linux VM

The [tagged renderer](https://github.com/vitral-ai/vitral-host-agent-releases/tree/connect-vm-publish-v0.4.6/connect)
ships with the immutable [Connect VM v0.4.6 release](https://github.com/vitral-ai/vitral-host-agent-releases/releases/tag/connect-vm-publish-v0.4.6).
It bundles signed Vitral Host v0.4.6 and the CSL v0.5.8 workload collector.

| Architecture | Archive | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| x86-64 | `vitral-connect-linux-amd64.tar` | 12032000 | `39715be8237a8c208b264a64d8ab5a4554994c5697ba32762c0d85b55577e284` |
| AArch64 | `vitral-connect-linux-arm64.tar` | 11304960 | `9595fdf919553bf9445262784c2973a7ef95b60e0985a6681bfbb87e25341d36` |

The VM needs systemd, Python 3.11 or later, OpenSSH and an existing nonroot
account. Render its user data with `python3 connect/cloud_init.py --help`.
Set `--url` to the selected archive under
`https://github.com/vitral-ai/vitral-host-agent-releases/releases/download/connect-vm-publish-v0.4.6/`,
and supply `--size` and `--sha256`. The installer verifies the tagged GitHub
Actions signer before checking and extracting the archive.

Authorize the OIDC rule or one-use join token in Machines and runtimes. Place
the credential on the VM through the provider's authenticated guest channel.
Use a root-owned regular file with mode 0400 or 0600, under root-controlled
directories, and give only its path to `--credential`. Supply the file before the
installation command runs. On a guest that boots before the credential arrives,
rerun the rendered installation command after the handoff.

Add `--install-agent` to install the pinned full package for the selected
agent. The updated renderer installs `procps` or `procps-ng` for Codex when
`ps` is missing. Codex retains the package resources used by its default
daemon. The tagged v0.4.6 renderer expects `ps` to be present already.
If the installed Vitral host supports `install-session-hook codex`, the
owner-scoped setup registers and authorizes that exact SessionStart hook.

The updated renderer also installs the CSL v0.5.8 personal tooling, pinned by
source digest. In the owner's Bash login shell, run `command -v csl
csl-resume-action` to verify both launchers. Personal collector sign-in is a
separate owner action.

Once enrollment succeeds, check the host and collector service status. Sign in
to your agent with your own account and start it in the host keeper.


The bootstrap enables systemd linger for the service account passed as
`--owner`, so its tmux panes can survive the last SSH logout.
