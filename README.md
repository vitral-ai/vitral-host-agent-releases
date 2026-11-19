# Vitral host-agent releases

Signed Linux host-agent executables and verification bundles are published here
as immutable releases using tags `host-agent-v<semver>`.

Each architecture has a `vitral-host-agent-linux-<arch>` executable, its
`.sigstore.json` verification bundle, and a published SHA-256 commitment.
Consumers verify the pinned digest and the exact keyless signing identity:

```
https://github.com/vitral-ai/vitralai/.github/workflows/host-agent-release.yml@refs/tags/host-agent-v<semver>
```

OIDC issuer: `https://token.actions.githubusercontent.com`.

A release is published after its producer and consumer qualification gates pass.
