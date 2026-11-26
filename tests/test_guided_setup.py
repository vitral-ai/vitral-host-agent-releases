import hashlib
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAG = "host-agent-publish-v0.4.0"
BINARIES = [
    "vitral-host-darwin-amd64", "vitral-host-darwin-arm64",
    "vitral-host-linux-amd64", "vitral-host-linux-arm64",
    "vitral-host-windows-amd64.exe", "vitral-host-windows-arm64.exe",
]


class GuidedSetupTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != "linux" or os.geteuid() == 0:
            self.skipTest("The guided shell setup runs as a normal Linux user")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ("setup.sh", "install.sh", "verify-setup-tree.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.platform = "linux-arm64" if os.uname().machine == "aarch64" else "linux-amd64"
        self.pins = []
        self.assets = self.root / "release"
        self.assets.mkdir()
        self.bin = self.root / "test-bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import shutil, sys\n"
            "args = sys.argv[1:]\n"
            f"source = Path({str(self.root)!r}) / args[-1].rsplit('/', 1)[-1]\n"
            "shutil.copyfile(source, args[args.index('--output') + 1])\n"
        )
        curl.chmod(0o700)

    def add_dependency(self, name, data, platform=None):
        download = self.root / name
        download.write_bytes(data)
        self.pins.append("\t".join([
            platform or self.platform, name, hashlib.sha256(data).hexdigest(),
            str(len(data)), f"https://fixture.invalid/{name}",
        ]))
        return download

    def add_archive(self, name, path, content, platform=None):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as stream:
            info = tarfile.TarInfo(path)
            info.size = len(content)
            info.mode = 0o700
            stream.addfile(info, io.BytesIO(content))
        return self.add_dependency(name, archive.getvalue(), platform)

    def install_fixture(self):
        self.add_dependency("cosign", b"#!/bin/sh\nexit 0\n")
        runner = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'.encode()
        self.add_archive("python", "python/bin/python3", runner)
        self.add_archive("csl", "csl-pinned/csl_sync.py", b"# source fixture\n", "all")
        self.add_archive("tmux", "tmux", b"#!/bin/sh\nexit 0\n")
        host = b'''#!/bin/sh
if [ "$1" = setup ] && [ "$2" = --help ]; then exit 0; fi
printf '%s\n' "$@" > "$VITRAL_TEST_ARGUMENTS"
'''
        digest = hashlib.sha256(host).hexdigest()
        (self.assets / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name in BINARIES))
        (self.assets / "SHA256SUMS.sigstore.json").write_text("{}")
        (self.assets / f"vitral-host-{self.platform}").write_bytes(host)

    def setup(self):
        (self.root / "setup-dependencies.tsv").write_text("\n".join(self.pins) + "\n")
        return subprocess.run([
            "bash", str(self.root / "setup.sh"), "--tag", TAG,
            "--api", "https://api.example", "--registry", "https://registry.example",
            "--discovery-url", "https://sign-in.example", "--client-id", "fixture",
            "--data-dir", str(self.root / "data"), "--assets-dir", str(self.assets),
        ], check=False, text=True, capture_output=True, env={
            **os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
            "VITRAL_TUNNEL_HOME": str(self.root / "host-state"),
            "VITRAL_TEST_ARGUMENTS": str(self.root / "arguments"),
        })

    def test_rootless_setup_uses_verified_dependency_paths(self):
        self.install_fixture()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = (self.root / "arguments").read_text().splitlines()
        python = Path(arguments[arguments.index("--collector-runtime") + 1])
        source = Path(arguments[arguments.index("--collector-source") + 1])
        self.assertTrue(python.is_file())
        self.assertTrue((source / "csl_sync.py").is_file())
        self.assertTrue((self.root / "host-state" / "tmux" / "bin" / "tmux").is_file())

    def test_bad_dependency_is_rejected_before_host_setup(self):
        self.install_fixture()
        (self.root / "cosign").write_bytes(b"#!/bin/sh\nexit 1\n")
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Verification failed for cosign", result.stderr)
        self.assertFalse((self.root / "arguments").exists())
        self.assertFalse((self.root / "host-state").exists())

    def test_tampered_retained_cosign_is_rejected_before_running(self):
        self.install_fixture()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        verifier, = (self.root / "data").glob("cosign-*/cosign")
        verifier.write_text('#!/bin/sh\nprintf "unsafe verifier" >&2\n')
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The retained Cosign failed verification.", result.stderr)
        self.assertNotIn("unsafe verifier", result.stderr)

    def test_tampered_source_is_rejected_on_rerun(self):
        self.install_fixture()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        module, = (self.root / "data").glob("csl-*/csl_sync.py")
        module.write_text("tampered")
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The retained csl dependency failed verification.", result.stderr)

    def test_generated_bytecode_is_removed_before_reusing_source(self):
        self.install_fixture()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        module, = (self.root / "data").glob("csl-*/csl_sync.py")
        bytecode = module.parent / "__pycache__" / "csl_sync.cpython-313.pyc"
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"unverified file")
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(bytecode.exists())
