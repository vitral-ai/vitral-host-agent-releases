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
from sign_connect_vm_release import CONNECT_VM_PROFILE


class SigningTests(unittest.TestCase):
    tag = "host-agent-publish-v0.0.0-dryrun.3"
    sha = "b" * 40

    def event(self, tag, profile=signer.HOST_PROFILE):
        ref = f"refs/tags/{tag}"
        return {"GITHUB_REPOSITORY": signer.REPO, "GITHUB_REPOSITORY_ID": signer.REPO_ID,
                "GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "tag", "GITHUB_REF": ref,
                "GITHUB_SHA": self.sha, "GITHUB_WORKFLOW_SHA": self.sha,
                "GITHUB_WORKFLOW_REF": f"{signer.REPO}/{profile.workflow}@{ref}"}

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

    def test_connect_vm_context_keeps_signing_gates_and_identities_separate(self):
        profile = CONNECT_VM_PROFILE
        tag = "connect-vm-publish-v1.2.3"
        env = self.event(tag, profile)
        with self.assertRaisesRegex(ValueError, "production signing requires"):
            signer.context({**env, signer.HOST_PROFILE.production_gate: "true"}, profile)
        env[profile.production_gate] = "true"
        self.assertEqual(signer.context(env, profile), (tag, False))
        with self.assertRaisesRegex(ValueError, "context is invalid"):
            signer.context(env)
        with self.assertRaisesRegex(ValueError, "context is invalid"):
            signer.context({**env, "GITHUB_WORKFLOW_SHA": "c" * 40}, profile)
        draft_tag = "connect-vm-publish-v0.0.0-dryrun.1"
        self.assertEqual(signer.context(self.event(draft_tag, profile), profile), (draft_tag, True))

    def test_connect_vm_draft_requires_two_bounded_archives_and_exact_manifest(self):
        profile = CONNECT_VM_PROFILE
        names = (*profile.payloads, signer.SUMS)
        assets = [
            {"id": i, "name": name, "size": signer.PAYLOAD_LIMIT if name in profile.payloads else 1,
             "state": "uploaded", "digest": "sha256:" + "a" * 64}
            for i, name in enumerate(names, 1)
        ]
        self.assertEqual(set(signer.asset_records({"assets": assets}, profile=profile)), set(names))
        for size in (0, signer.PAYLOAD_LIMIT + 1):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "invalid draft asset metadata"):
                signer.asset_records({"assets": [{**assets[0], "size": size}, *assets[1:]]}, profile=profile)
        for invalid in (assets[1:], [*assets, {**assets[0], "name": "render.py", "id": 4}]):
            with self.assertRaisesRegex(ValueError, "unexpected draft asset set"):
                signer.asset_records({"assets": invalid}, profile=profile)

        payload = {name: name.encode() for name in profile.payloads}
        manifest = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in payload.items())
        for correct in (True, False):
            with self.subTest(correct=correct):
                staged = self.root / str(correct)
                staged.mkdir()
                blobs = {**payload, signer.SUMS: (manifest if correct else manifest.replace(".tar", ".tar.gz")).encode()}
                records = {name: {"id": i, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                           for i, (name, data) in enumerate(blobs.items(), 1)}

                def api(asset, *args, output=None):
                    name = next(name for name, record in records.items() if asset.endswith(f"/{record['id']}"))
                    output.write_bytes(blobs[name])

                if correct:
                    signer.verify_assets(records, staged, api, profile=profile)
                else:
                    with self.assertRaisesRegex(ValueError, "checksum manifest differs"):
                        signer.verify_assets(records, staged, api, profile=profile)

    def test_connect_vm_cosign_pins_its_own_exact_tag_identity(self):
        profile = CONNECT_VM_PROFILE
        tag = "connect-vm-publish-v1.2.3"
        calls = []
        with patch.dict(os.environ, {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "token", "ACTIONS_ID_TOKEN_REQUEST_URL": "url"}):
            signer.sign_and_verify(self.root / "cosign", tag, False, self.assets,
                                   run=lambda command, **_: calls.append(command), profile=profile)
        self.assertEqual([command[1] for command in calls], ["sign-blob", "verify-blob"])
        self.assertIn(f"https://github.com/{signer.REPO}/{profile.workflow}@refs/tags/{tag}", calls[1])
        self.assertIn(signer.ISSUER, calls[1])

    def test_verified_draft_bundle_is_uploaded_and_tampered_draft_is_rejected(self):
        for profile, tag in (
            (signer.HOST_PROFILE, self.tag),
            (CONNECT_VM_PROFILE, "connect-vm-publish-v0.0.0-dryrun.3"),
        ):
            with self.subTest(profile=profile.prefix):
                self.check_draft_signing(profile, tag)

    def check_draft_signing(self, profile, tag):
        source_dir = self.root / profile.prefix
        source_dir.mkdir()
        for filename in profile.payloads:
            (source_dir / filename).write_text(filename)
        (source_dir / signer.SUMS).write_text("".join(
            f"{signer.digest(source_dir / filename)}  {filename}\n" for filename in profile.payloads))

        for tampered in (True, False):
            with self.subTest(tampered=tampered):
                output = self.root / (profile.prefix + str(tampered))
                output.mkdir()
                assets = {
                    path.name: {"id": index, "name": path.name, "state": "uploaded",
                                "size": path.stat().st_size, "digest": "sha256:" + signer.digest(path)}
                    for index, path in enumerate(sorted(source_dir.iterdir()), 1)
                }
                flags = {"signed": False, "uploaded": False}
                reads = 0

                def api(*arguments, output=None):
                    nonlocal reads
                    path = arguments[0]
                    if path == "--paginate":
                        reads += 1
                        listed = [{**asset, "download_count": reads} for asset in assets.values()]
                        return [[{"tag_name": tag, "draft": True, "id": 23, "assets": listed}]]
                    if path == "--method":
                        self.assertEqual(arguments[:3], ("--method", "POST",
                                         f"https://uploads.github.com/{signer.BASE}/releases/23/assets?name={signer.BUNDLE}"))
                        data = Path(arguments[-1]).read_bytes()
                        assets[signer.BUNDLE] = {
                            "id": len(assets) + 1, "name": signer.BUNDLE, "state": "uploaded", "size": len(data),
                            "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                        flags["uploaded"] = True
                        return {}
                    asset = next(item for item in assets.values() if path.endswith(f"/{item['id']}"))
                    source = (source_dir / asset["name"]).read_bytes()
                    output.write_bytes(b"X" + source[1:] if tampered and asset["name"] in profile.payloads else source)

                def sign(cosign, signed_tag, staging, output):
                    self.assertEqual(signed_tag, tag)
                    self.assertTrue(staging)
                    (output / signer.BUNDLE).write_text("fixture bundle")
                    flags["signed"] = True

                env = self.event(tag, profile)
                if tampered:
                    with self.assertRaisesRegex(ValueError, "downloaded bytes differ"):
                        signer.sign_draft(env, output, api=api, acquire=lambda _: None, sign=sign, profile=profile)
                else:
                    receipt = signer.sign_draft(env, output, api=api, acquire=lambda _: None, sign=sign, profile=profile)
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
        installer_tag = "host-agent-publish-v0.2.0"
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
        expected_identity = f"https://github.com/{signer.REPO}/{signer.WORKFLOW}@refs/tags/{installer_tag}"
        bundle = self.assets / signer.BUNDLE
        bundle.write_text(expected_identity)
        destination = self.root / "bin"
        binary = destination / "vitral-host"
        env = {**os.environ, "PATH": str(verifier_dir) + os.pathsep + os.environ["PATH"]}
        command = [str(ROOT / "install.sh"), "--tag", installer_tag, "--assets-dir", str(self.assets),
                   "--bin-dir", str(destination)]
        platform = verifier_dir / "uname"
        platform.write_text("#!/bin/sh\ncase \"$1\" in -s) echo \"$TEST_OS\";; -m) echo \"$TEST_CPU\";; esac\n")
        platform.chmod(0o755)
        sysctl = verifier_dir / "sysctl"
        sysctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$TEST_ARM64\"\n")
        sysctl.chmod(0o755)
        if sys.platform != "darwin":
            shasum = verifier_dir / "shasum"
            shasum.write_text('#!/bin/sh\n[ "$*" = "-a 256" ] || exit 1\nexec sha256sum\n')
            shasum.chmod(0o755)
            move = verifier_dir / "mv"
            move.write_text('#!/bin/sh\nshift\nexec /usr/bin/mv -fT "$@"\n')
            move.chmod(0o755)
        for os_name, machine, arm64, filename in (
            ("Linux", "x86_64", "0", "vitral-host-linux-amd64"),
            ("Linux", "aarch64", "1", "vitral-host-linux-arm64"),
            ("Darwin", "x86_64", "0", "vitral-host-darwin-amd64"),
            ("Darwin", "arm64", "1", "vitral-host-darwin-arm64"),
            ("Darwin", "x86_64", "1", "vitral-host-darwin-arm64"),
        ):
            with self.subTest(os_name=os_name, machine=machine, arm64=arm64):
                env.update(TEST_OS=os_name, TEST_CPU=machine, TEST_ARM64=arm64)
                result = subprocess.run(command, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(binary.read_text(), filename)
        env.update(TEST_OS="Linux", TEST_CPU="x86_64", TEST_ARM64="0")
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
        manifest = self.assets / signer.SUMS
        full = manifest.read_bytes()
        manifest.write_bytes(b"".join(line for line in full.splitlines(keepends=True) if b"-linux-" in line))
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertIn("Incomplete signed checksum manifest", result.stderr)
        bundle.unlink()
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertIn("Missing release asset " + signer.BUNDLE, result.stderr)
        self.assertEqual(binary.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
