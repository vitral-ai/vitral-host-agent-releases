"""Install the pinned personal CSL commands without creating user state."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
from urllib.request import HTTPSHandler, HTTPRedirectHandler, build_opener

SOURCE = '545dc0c2ec0ce98c9af7ca774ee7b2af2f8a5641'
URL = 'https://codeload.github.com/vitral-ai/context-session-librarian/tar.gz/' + SOURCE
SHA256 = '9014ff7d769f8bf67347c5bf4c9bd468d9b10db8a9fefecbe2f83a9deca70807'
SIZE = 1487715
COMMANDS = ('csl', 'csl-resume-action')


class Direct(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download():
    with build_opener(HTTPSHandler(), Direct()).open(URL, timeout=40) as response:
        return response.read(SIZE + 1)


def install(destination, bin_dir, fetch=download):
    marker = destination / '.source-sha256'
    if destination.is_symlink() or marker.is_symlink():
        raise ValueError('Invalid personal CSL installation path')
    for command in COMMANDS:
        link, target = bin_dir / command, destination / command
        if (link.exists() or link.is_symlink()) and not (link.is_symlink() and link.readlink() == target):
            raise ValueError('Refusing to replace an existing personal CSL launcher')
    if destination.exists():
        if (not marker.is_file() or marker.read_text() != SHA256
                or any(not (destination / name).is_file() or (destination / name).is_symlink()
                       for name in COMMANDS)):
            raise ValueError('Existing personal CSL installation differs from the pin')
    else:
        payload = fetch()
        if len(payload) != SIZE or hashlib.sha256(payload).hexdigest() != SHA256:
            raise ValueError('Personal CSL archive differs from the release pin')
        with tempfile.TemporaryDirectory(prefix='.vitral-csl-', dir=destination.parent) as temporary:
            stage = Path(temporary) / 'source'
            stage.mkdir(mode=0o755)
            remaining = 32 << 20
            top = 'context-session-librarian-' + SOURCE
            with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
                for index, entry in enumerate(archive):
                    path = PurePosixPath(entry.name)
                    if (index >= 2000 or not path.parts or path.parts[0] != top or '..' in path.parts
                            or not (entry.isdir() or entry.isreg()) or entry.sparse):
                        raise ValueError('Unexpected personal CSL archive member')
                    if entry.isdir():
                        continue
                    remaining -= entry.size
                    if remaining < 0:
                        raise ValueError('Personal CSL source exceeds its expanded size limit')
                    target = stage.joinpath(*path.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with archive.extractfile(entry) as source, target.open('xb') as stream:
                        stream.write(source.read())
                    target.chmod(0o755 if entry.mode & 0o111 else 0o644)
            if not all((stage / name).is_file() for name in (*COMMANDS, 'VERSION')):
                raise ValueError('Personal CSL release is missing its entry points')
            (stage / marker.name).write_text(SHA256)
            stage.rename(destination)
    for command in COMMANDS:
        link = bin_dir / command
        if not link.is_symlink():
            link.symlink_to(destination / command)


if __name__ == '__main__':
    os.umask(0o022)
    install(Path('/opt/vitral-personal-csl'), Path('/usr/local/bin'))
