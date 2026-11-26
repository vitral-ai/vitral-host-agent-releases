"""Install the native agent versions accepted by the paired workload collector."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


PINS = {
    'x86_64': (
        ('codex', 'https://registry.npmjs.org/@openai/codex/-/codex-0.169.0-linux-x64.tgz',
         'o05NyAOCDygSPRo1yinzxvkRSdmsZyBD9E7mbpmjRanSfM8lypqWtQgEo4XfNkwhuCR2aFu8mlGOsQGFXVbVww==',
         'vendor/x86_64-unknown-linux-musl/bin/codex', 'codex-cli 0.169.0'),
        ('claude', 'https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.308.tgz',
         'ffUryevpH7POStPJ/xpUI5R1ozYbH9o/Dbh/xPSmyVJehELnRMZmRnejdQmq0TejOW/SbNnJFhCGMgwIsOqFTQ==',
         'claude', '2.1.308 (Claude Code)'),
    ),
    'aarch64': (
        ('codex', 'https://registry.npmjs.org/@openai/codex/-/codex-0.169.0-linux-arm64.tgz',
         'NZ/DaQUpcQmxmqee7Z9uC1rzw3GhSbooawcaTe1LN5Yk+WlinUKGdTXJgClRT/9ogQEEM9TWxYzvxPfj5G/xFg==',
         'vendor/aarch64-unknown-linux-musl/bin/codex', 'codex-cli 0.169.0'),
        ('claude', 'https://registry.npmjs.org/@anthropic-ai/claude-code-linux-arm64/-/claude-code-linux-arm64-2.1.308.tgz',
         'xtT2Cd7n+yZN9PiWbWHaZf9w25YflV+7VOX5IBPB/IWErKZFhsst2/Ay1tSgq/Mzp1vp56xwFRqPa6UAI9BZIw==',
         'claude', '2.1.308 (Claude Code)'),
    ),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, archive):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=30) as response, archive.open('xb') as output:
        remaining = 256 << 20
        while chunk := response.read(1 << 20):
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError('Agent package exceeds the download limit')
            output.write(chunk)


def extract(archive, root, integrity):
    with archive.open('rb') as source:
        actual = base64.b64encode(hashlib.file_digest(source, 'sha512').digest()).decode('ascii')
    if actual != integrity:
        raise ValueError('Agent package digest differs from the release pin')
    remaining = 512 << 20
    with tarfile.open(archive, 'r:gz') as package:
        for index, item in enumerate(package):
            path = Path(item.name)
            if (index >= 1000 or not item.isreg() or item.sparse or len(path.parts) < 2
                    or '/'.join(path.parts) != item.name or path.parts[0] != 'package' or '..' in path.parts):
                raise ValueError('Unexpected agent package member')
            remaining -= item.size
            if remaining < 0:
                raise ValueError('Agent package exceeds the expanded size limit')
            target = root.joinpath(*path.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(item) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if item.mode & 0o111 else 0o644)


def install(destination, commands, machine, *, only=None, download=fetch):
    pins = PINS.get(machine)
    if not pins:
        raise ValueError('Unsupported architecture for the pinned native agents')
    if only is not None:
        pins = [pin for pin in pins if pin[0] == only]
        if not pins:
            raise ValueError('Unknown pinned native agent')
    links = {commands / name: destination / name / binary for name, _, _, binary, _ in pins}
    if destination.is_symlink():
        raise ValueError('Refusing to replace an existing agent installation')
    for link, binary in links.items():
        if (link.exists() or link.is_symlink()) and (not link.is_symlink() or link.readlink() != binary):
            raise ValueError('Refusing to replace an existing agent command')
    receipt = '.agent-archive-pins'
    expected = json.dumps({name: digest for name, _, digest, _, _ in pins}, sort_keys=True)
    if destination.exists():
        marker = destination / receipt
        if (marker.is_symlink() or not marker.is_file() or marker.read_text() != expected
                or any(not binary.is_file() or binary.resolve() != binary for binary in links.values())):
            raise ValueError('Existing managed tools differ from the pinned installation')
    else:
        with tempfile.TemporaryDirectory(prefix='.vitral-agents-', dir=destination.parent) as directory:
            temporary = Path(directory)
            staged = temporary / 'agents'
            staged.mkdir(mode=0o755)
            empty_home = temporary / 'home'
            empty_home.mkdir(mode=0o700)
            for name, url, integrity, binary, version in pins:
                archive = temporary / (name + '.tar.gz')
                download(url, archive)
                extract(archive, staged / name, integrity)
                actual = subprocess.check_output(
                    [str(staged / name / binary), '--version'],
                    env={'HOME': str(empty_home), 'PATH': os.defpath}, text=True, timeout=20,
                ).strip()
                if actual != version:
                    raise ValueError('Installed agent version differs from the collector profile')
            (staged / receipt).write_text(expected)
            staged.rename(destination)
    for link, binary in links.items():
        if not link.is_symlink():
            link.symlink_to(binary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=('codex', 'claude'))
    options = parser.parse_args()
    install(Path('/opt/vitral-agent-tools'), Path('/usr/local/bin'), platform.machine(),
            only=options.only)
