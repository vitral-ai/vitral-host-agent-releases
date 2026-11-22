import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sign_host_release as signer


class SigningTests(unittest.TestCase):
    tag = "host-agent-publish-v0.0.0-dryrun.1"
    sha = "b" * 40

    def event(self, tag):
        ref = f"refs/tags/{tag}"
        return {"GITHUB_REPOSITORY": signer.REPO, "GITHUB_REPOSITORY_ID": signer.REPO_ID,
                "GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "tag", "GITHUB_REF": ref,
                "GITHUB_SHA": self.sha, "GITHUB_WORKFLOW_SHA": self.sha,
                "GITHUB_WORKFLOW_REF": f"{signer.REPO}/{signer.WORKFLOW}@{ref}"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        for filename in signer.BINARIES:
            (self.assets / filename).write_text(filename)
        (self.assets / signer.SUMS).write_text("".join(
            f"{signer.digest(self.assets / filename)}  {filename}\n" for filename in signer.BINARIES))

    def test_staging_selection_and_production_approval(self):
        self.assertEqual(signer.context(self.event(self.tag)), (self.tag, True))
        with self.assertRaisesRegex(ValueError, "production signing requires"):
            signer.context(self.event("host-agent-publish-v1.2.3"))
        with self.assertRaisesRegex(ValueError, "context is invalid"):
            signer.context({**self.event(self.tag), "GITHUB_WORKFLOW_REF": "another workflow"})

    def test_verified_draft_bundle_is_uploaded_and_tampered_draft_is_rejected(self):
        for tampered in (True, False):
            with self.subTest(tampered=tampered):
                output = self.root / str(tampered)
                output.mkdir()
                assets = {
                    path.name: {"id": index, "name": path.name, "state": "uploaded",
                                "size": path.stat().st_size, "digest": "sha256:" + signer.digest(path)}
                    for index, path in enumerate(sorted(self.assets.iterdir()), 1)
                }
                flags = {"signed": False, "uploaded": False}
                reads = 0

                def api(*arguments, output=None):
                    nonlocal reads
                    path = arguments[0]
                    if path == "--paginate":
                        reads += 1
                        listed = [{**asset, "download_count": reads} for asset in assets.values()]
                        return [[{"tag_name": self.tag, "draft": True, "id": 23, "assets": listed}]]
                    if path == "--method":
                        self.assertEqual(arguments[:3], ("--method", "POST",
                                         f"https://uploads.github.com/{signer.BASE}/releases/23/assets?name={signer.BUNDLE}"))
                        data = Path(arguments[-1]).read_bytes()
                        assets[signer.BUNDLE] = {
                            "id": 4, "name": signer.BUNDLE, "state": "uploaded", "size": len(data),
                            "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                        flags["uploaded"] = True
                        return {}
                    asset = next(item for item in assets.values() if path.endswith(f"/{item['id']}"))
                    source = (self.assets / asset["name"]).read_bytes()
                    output.write_bytes(b"X" + source[1:] if tampered and asset["name"] in signer.BINARIES else source)

                def sign(cosign, tag, staging, output):
                    self.assertEqual(tag, self.tag)
                    self.assertTrue(staging)
                    (output / signer.BUNDLE).write_text("fixture bundle")
                    flags["signed"] = True

                if tampered:
                    with self.assertRaisesRegex(ValueError, "downloaded bytes differ"):
                        signer.sign_draft(self.event(self.tag), output, api=api, acquire=lambda _: None, sign=sign)
                else:
                    receipt = signer.sign_draft(self.event(self.tag), output, api=api, acquire=lambda _: None, sign=sign)
                    self.assertTrue(receipt["draft"])
                self.assertEqual(flags, {"signed": not tampered, "uploaded": not tampered})

    def test_cosign_staging_uses_only_staging_roots_and_endpoints(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs["env"]))
            if command[1] == "initialize":
                targets = Path(kwargs["env"]["TUF_ROOT"]) / "tuf-repo-cdn.sigstage.dev/targets"
                targets.mkdir(parents=True)
                config = {group: [{"url": "https://example.sigstage.dev"}]
                          for group in ("caUrls", "rekorTlogUrls", "tsaUrls", "oidcUrls")}
                (targets / "signing_config.v0.2.json").write_text(json.dumps(config))

        with patch.dict(os.environ, {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "token", "ACTIONS_ID_TOKEN_REQUEST_URL": "url"}):
            signer.sign_and_verify(self.root / "cosign", self.tag, True, self.assets, run=run)
        self.assertEqual([command[1] for command, _ in calls], ["initialize", "sign-blob", "verify-blob"])
        self.assertIn("--staging", calls[0][0])
        self.assertIn("--signing-config", calls[1][0])
        self.assertIn(f"https://github.com/{signer.REPO}/{signer.WORKFLOW}@refs/tags/{self.tag}", calls[2][0])
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", calls[2][1])

    def test_installer_checks_exact_identity_and_refuses_bad_or_missing_assets(self):
        verifier_dir = self.root / "tools"
        verifier_dir.mkdir()
        fake = verifier_dir / "cosign"
        fake.write_text("""#!/bin/bash
set -eu
if [[ $1 == initialize && $2 == --staging ]]; then
    mkdir -p "$TUF_ROOT/tuf-repo-cdn.sigstage.dev/targets"
    printf fixture > "$TUF_ROOT/tuf-repo-cdn.sigstage.dev/targets/trusted_root.json"
    exit 0
fi
[[ $1 == verify-blob ]] && shift
while (( $# > 1 )); do
    case "$1" in
        --bundle) bundle=$2 ;;
        --certificate-identity) identity=$2 ;;
        --certificate-oidc-issuer) [[ $2 == https://token.actions.githubusercontent.com ]] || exit 7 ;;
        --trusted-root) [[ -s $2 ]] || exit 8 ;;
        --use-signed-timestamps) shift; continue ;;
        *) exit 9 ;;
    esac
    shift 2
done
[[ $(cat "$bundle") == "$identity" ]]
""")
        fake.chmod(0o755)
        expected_identity = f"https://github.com/{signer.REPO}/{signer.WORKFLOW}@refs/tags/{self.tag}"
        bundle = self.assets / signer.BUNDLE
        bundle.write_text(expected_identity)
        destination = self.root / "bin"
        binary = destination / "vitral-host"
        env = {**os.environ, "PATH": str(verifier_dir) + os.pathsep + os.environ["PATH"]}
        command = [str(ROOT / "install.sh"), "--tag", self.tag, "--assets-dir", str(self.assets),
                   "--bin-dir", str(destination)]
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.access(binary, os.X_OK))
        original = binary.read_bytes()
        bundle.write_text(expected_identity.replace("sign-host-release", "other"))
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertIn("signature verification failed", result.stderr)
        bundle.write_text(expected_identity)
        (self.assets / original.decode()).write_bytes(b"tampered")
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertIn("binary checksum verification failed", result.stderr)
        bundle.unlink()
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertIn("Missing release asset " + signer.BUNDLE, result.stderr)
        self.assertEqual(binary.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
