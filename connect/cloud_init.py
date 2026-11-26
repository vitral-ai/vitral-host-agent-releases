"""Render a Linux VM bootstrap from a pinned public Vitral Connect bundle."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit
from uuid import UUID

ROOT = '/opt/vitral-connect'
STATE = '/var/lib/vitral-connect'
MANAGED_TOOLS = '/usr/local/libexec/vitral-connect-managed-tools.py'
HANDOFF = '/usr/local/libexec/vitral-connect-managed-handoff.py'
HANDOFF_CREDENTIAL = '/var/lib/vitral-connect-bootstrap/token'
PROFILE = {'codex': 'codex-0.169.0-text-v1', 'claude': 'claude-2.1.308-text-v1'}
RELEASE_PREFIX = 'https://github.com/vitral-ai/vitral-host-agent-releases/releases/download/'
RELEASE_PATH = r'connect-vm-publish-v[0-9]+[.][0-9]+[.][0-9]+/vitral-connect-linux-(?:amd64|arm64)[.]tar'
FETCH = '''
import hashlib, os, pathlib, re, shutil, stat, subprocess, sys, tarfile, tempfile, urllib.request
from urllib.parse import urlsplit

RELEASE_PREFIX = RELEASE_ROOT
RELEASE_PATH = RELEASE_PATTERN
COSIGN_PREFIX = 'https://github.com/sigstore/cosign/releases/download/v3.1.4/'
COSIGN = {
    'amd64': ('424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa', 145688404),
    'arm64': ('ade83b7aac62e131cacfe96d958948b2150cc9efec46a570a2649b8e27dedad2', 136437761),
}


def release_asset(url):
    asset = urlsplit(url)
    return (asset.scheme == 'https' and asset.netloc in
            ('release-assets.githubusercontent.com', 'objects.githubusercontent.com')
            and not asset.fragment)


def release_origin(url):
    return url.startswith((RELEASE_PREFIX, COSIGN_PREFIX))


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (release_origin(req.full_url) or release_asset(req.full_url)) and release_asset(newurl):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def download(opener, url, target, limit, *, exact=False, digest=None):
    with opener.open(url, timeout=30) as response, target.open('xb') as output:
        actual = response.geturl()
        if actual != url and not (release_origin(url) and release_asset(actual)):
            raise ValueError('Unexpected bundle endpoint')
        remaining = limit
        while remaining:
            chunk = response.read(min(1024 * 1024, remaining))
            if not chunk:
                if exact:
                    raise ValueError('Bundle ended before its pinned length')
                break
            output.write(chunk)
            remaining -= len(chunk)
        if response.read(1):
            raise ValueError('Bundle exceeds its permitted length')
    with target.open('rb') as source:
        if digest is not None and hashlib.file_digest(source, 'sha256').hexdigest() != digest:
            raise ValueError('Expected a verified bootstrap bundle')


def verify_release(opener, url, digest, directory):
    match = re.fullmatch(RELEASE_PATH, url.removeprefix(RELEASE_PREFIX))
    if match is None:
        raise ValueError('Unexpected Connect release asset')
    tag, name = url.removeprefix(RELEASE_PREFIX).split('/')
    arch = name.removeprefix('vitral-connect-linux-').removesuffix('.tar')
    if {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(os.uname().machine) != arch:
        raise ValueError('Connect release does not match this architecture')
    root = RELEASE_PREFIX + tag + '/'
    sums, bundle, cosign = (directory / name for name in ('SHA256SUMS', 'SHA256SUMS.sigstore.json', 'cosign'))
    download(opener, root + sums.name, sums, 64 << 10)
    download(opener, root + bundle.name, bundle, 1 << 20)
    cosign_digest, cosign_size = COSIGN[arch]
    download(opener, COSIGN_PREFIX + 'cosign-linux-' + arch, cosign,
             cosign_size, exact=True, digest=cosign_digest)
    cosign.chmod(0o700)
    subprocess.run([str(cosign), 'verify-blob', '--bundle', str(bundle), '--certificate-identity',
                    'https://github.com/vitral-ai/vitral-host-agent-releases/.github/workflows/'
                    'sign-connect-vm-release.yml@refs/tags/' + tag,
                    '--certificate-oidc-issuer', 'https://token.actions.githubusercontent.com', str(sums)],
                   check=True, timeout=90)
    selected = [line for line in sums.read_text(encoding='ascii').splitlines()
                if len(line.split()) == 2 and line.split()[1] == name]
    if selected != [digest + '  ' + name]:
        raise ValueError('Pinned bundle digest differs from the signed release')

arguments = sys.argv[1:]
prepare = []
if arguments[0] == '--prepare-only':
    _, handoff, organization, environment, *arguments = arguments
    prepare = [sys.executable, handoff, 'prepare', organization, environment]
url, digest, length, credential, *managed_tools = arguments
length = int(length)

def check_credential(credential):
    complete = pathlib.Path('/var/lib/vitral-connect/enrollment-complete')
    # A user's completion marker can allow an absent credential. It never
    # vouches for the source file or any surviving ancestor.
    completed = not complete.is_symlink() and complete.is_file()
    for parent in reversed(pathlib.Path(credential).parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if not completed:
                raise
            break
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Enrollment requires a root-controlled credential path')
    try:
        fd = os.open(credential, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if not completed:
            raise
    else:
        with os.fdopen(fd, 'rb') as secret:
            info = os.fstat(secret.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise ValueError('Enrollment requires a root-only credential file')

if not prepare:
    check_credential(credential)
destination = pathlib.Path('/opt/vitral-connect')
with tempfile.TemporaryDirectory(prefix='vitral-connect-', dir=destination.parent) as temporary:
    temporary = pathlib.Path(temporary)
    archive = temporary / 'bootstrap.tar'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ReleaseRedirect())
    if url.startswith(RELEASE_PREFIX):
        verify_release(opener, url, digest, temporary)
    download(opener, url, archive, length, exact=True, digest=digest)
    stage = temporary / 'root'
    stage.mkdir()
    total = 0
    with tarfile.open(archive, 'r:*') as source:
        for index, item in enumerate(source):
            if (index >= 1000 or not item.isreg() or item.sparse
                    or not re.fullmatch(r'(?:bin|lib)/[A-Za-z0-9_./-]+', item.name)
                    or any(part in ('', '.', '..') for part in item.name.split('/'))):
                raise ValueError('Unexpected bundle member')
            total += item.size
            if total > 128 << 20:
                raise ValueError('Expanded bundle exceeds its limit')
            target = stage / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(item) as reader, target.open('xb') as writer:
                shutil.copyfileobj(reader, writer)
            target.chmod(0o755 if item.name.startswith('bin/') else 0o644)
    for name in ('vitral-host', 'vitral-connect-launch', 'csl-workload-collector'):
        if not (stage / 'bin' / name).is_file():
            raise ValueError('Bundle lacks a required Vitral command')
    marker = destination / '.bundle-sha256'
    if destination.is_symlink() or marker.is_symlink():
        raise ValueError('Unexpected Vitral installation path')
    if destination.exists():
        if not marker.is_file() or marker.read_text() != digest:
            raise ValueError('A different Vitral bundle is already installed')
    else:
        (stage / marker.name).write_text(digest)
        stage.rename(destination)
if managed_tools:
    subprocess.run([sys.executable, *managed_tools], check=True, timeout=300)
subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
if prepare:
    subprocess.run(prepare, check=True, timeout=30)
else:
    subprocess.run(['systemctl', 'enable', '--now', 'vitral-connect-host.service',
                    'vitral-connect-collector.service'], check=True, timeout=180)
    if managed_tools:
        subprocess.run(['systemctl', 'start', 'vitral-connect-wrapper.service'], check=True, timeout=30)
'''.replace('RELEASE_ROOT', repr(RELEASE_PREFIX)).replace('RELEASE_PATTERN', repr(RELEASE_PATH))


def path(value):
    if (not re.fullmatch(r'/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+', value)
            or any(part in ('.', '..') for part in value.split('/'))):
        raise ValueError('Use an absolute path with canonical components')
    return value


def render(*, owner, organization, origin, provider, cluster, method, credential, transcripts, url, sha256, size,
           claude_transcripts=None, deferred_environment=None, install_agent=False):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', owner) or owner == 'root':
        raise ValueError('Choose an existing nonroot account')
    if not re.fullmatch(r'[0-9a-f]{24}', organization):
        raise ValueError('Choose an organization ID')
    if not re.fullmatch(r'https://[a-z0-9][a-z0-9.-]{0,252}', origin):
        raise ValueError('Choose an HTTPS API origin on port 443')
    if provider not in PROFILE or method not in ('oidc', 'join_token'):
        raise ValueError('Choose a supported agent and enrollment method')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', cluster):
        raise ValueError('Choose a cluster identifier')
    path(credential)
    path(transcripts)
    if deferred_environment is not None:
        if (str(UUID(deferred_environment)) != deferred_environment or claude_transcripts is None
                or credential != HANDOFF_CREDENTIAL):
            raise ValueError('Choose the dedicated deferred enrollment path for a managed VM')
    if claude_transcripts is not None:
        secondary = PurePosixPath(path(claude_transcripts))
        primary = PurePosixPath(transcripts)
        if (provider != 'codex' or method != 'join_token' or secondary.is_relative_to(primary)
                or primary.is_relative_to(secondary)
                or any(secondary.is_relative_to(root) or root.is_relative_to(secondary)
                       for root in (PurePosixPath(STATE), PurePosixPath(ROOT)))):
            raise ValueError('Choose distinct paired sources for a managed cloud machine')
    asset = urlsplit(url)
    if (asset.scheme != 'https' or not asset.hostname or asset.username is not None
            or asset.port not in (None, 443) or asset.query or asset.fragment or not asset.path):
        raise ValueError('Choose a fixed HTTPS artifact URL')
    if (asset.hostname in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com')
            or asset.hostname == 'github.com' and (not url.startswith(RELEASE_PREFIX)
                or not re.fullmatch(RELEASE_PATH, url.removeprefix(RELEASE_PREFIX)))):
        raise ValueError('Choose an immutable Vitral Connect release asset')
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or type(size) is not int or not 0 < size <= 128 << 20:
        raise ValueError('Specify the bundle digest and exact size')

    environment = {
        'VITRAL_API': origin, 'VITRAL_TUNNEL_HOME': STATE + '/host',
        'VITRAL_TUNNEL_KIND': 'ssh', 'VITRAL_HOST_TERMINAL_MODE': 'embedded',
        'VITRAL_COLLECTOR_ORIGIN': origin, 'VITRAL_COLLECTOR_PROVIDER': provider,
        'VITRAL_COLLECTOR_PROFILE': PROFILE[provider], 'VITRAL_COLLECTOR_TRANSCRIPT_ROOT': transcripts,
        'VITRAL_COLLECTOR_STATE': STATE + '/collector', 'VITRAL_COLLECTOR_REDACTION': 'required',
        'VITRAL_LOCATION_LABEL': 'Vitral cloud VM', 'VITRAL_LOCATION_PROVIDER': 'vm',
        'VITRAL_LOCATION_CLUSTER': cluster, 'VITRAL_LOCATION_NAMESPACE': 'cloud-init',
    }
    dirs = [STATE + '/collector', transcripts]
    if claude_transcripts is not None:
        environment['VITRAL_COLLECTOR_CLAUDE_TRANSCRIPT_ROOT'] = claude_transcripts
        dirs.append(claude_transcripts)
    common = '\n'.join(f'Environment="{key}={value}"' for key, value in environment.items())
    common += ('\nEnvironment=VITRAL_LOCATION_WORKLOAD=%m\nEnvironment=VITRAL_LOCATION_REPLICA=%H\n'
               f'User={owner}\nUMask=0077\nStateDirectory=vitral-connect\nStateDirectoryMode=0700\n'
               'NoNewPrivileges=yes\n')
    reservation = ''
    if deferred_environment is not None:
        reservation = (f'Environment=VITRAL_CONNECT_EXPECTED_ENVIRONMENT={deferred_environment}\n'
                       'Environment=VITRAL_CONNECT_EXPECTED_GENERATION=1\n')
    enrollment = (f'{common}Type=oneshot\nRemainAfterExit=yes\n'
                  f'Environment=VITRAL_CONNECT_ORGANIZATION={organization}\n'
                  f'Environment=VITRAL_CONNECT_METHOD={method}\n'
                  f'{reservation}'
                  f'LoadCredential=token:{credential}\n'
                  'Environment=VITRAL_CONNECT_CREDENTIAL_FILE=%d/token\nPrivateMounts=yes\n'
                  f'Environment=VITRAL_HOST_BINARY={ROOT}/bin/vitral-host\n'
                  f'ExecStartPre=/usr/bin/install -d -m 0700 {" ".join(dirs)}\n'
                  f'ExecStart={ROOT}/bin/csl-workload-collector --connect-runtime\n'
                  f'ExecStartPost=/usr/bin/touch {STATE}/enrollment-complete\n')
    files = [{
        'path': '/etc/systemd/system/vitral-connect-enroll.service', 'permissions': '0644',
        'content': ('[Unit]\nAfter=network-online.target\nWants=network-online.target\n'
                    f'ConditionPathExists=!{STATE}/enrollment-complete\n[Service]\n' + enrollment),
    }]
    for name, argv in (('host', 'vitral-host run'), ('collector', 'csl-workload-collector')):
        files.append({
            'path': '/etc/systemd/system/vitral-connect-' + name + '.service', 'permissions': '0644',
            'content': ('[Unit]\nRequires=vitral-connect-enroll.service\nAfter=vitral-connect-enroll.service\n'
                        f'[Service]\n{common}InaccessiblePaths=-/run/credentials/vitral-connect-enroll.service\n'
                        f'ExecStart={ROOT}/bin/{argv}\n[Install]\nWantedBy=multi-user.target\n'),
        })
    installer = ['python3', '-c', FETCH, url, sha256, str(size), credential]
    if claude_transcripts is not None or install_agent:
        files.append({'path': MANAGED_TOOLS, 'permissions': '0644',
                      'content': Path(__file__).with_name('managed_tools.py').read_text()})
        files.append({
            'path': '/etc/systemd/system/vitral-connect-wrapper.service', 'permissions': '0644',
            'content': ('[Unit]\nAfter=vitral-connect-enroll.service\nRequires=vitral-connect-enroll.service\n'
                        f'ConditionPathExists={STATE}/enrollment-complete\n[Service]\n{common}Type=oneshot\n'
                        'InaccessiblePaths=-/run/credentials/vitral-connect-enroll.service\n'
                        f'ExecStart={ROOT}/bin/vitral-host install-agent-wrap\n'),
        })
        installer.append(MANAGED_TOOLS)
        if claude_transcripts is None:
            installer.extend(('--only', provider))
    if deferred_environment is not None:
        files.append({'path': HANDOFF, 'permissions': '0644',
                      'content': Path(__file__).with_name('managed_handoff.py').read_text()})
        installer[3:3] = ['--prepare-only', HANDOFF, organization, deferred_environment]
    return '#cloud-config\n' + json.dumps({
        'write_files': files,
        'runcmd': [installer],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('owner', 'organization', 'origin', 'provider', 'cluster', 'method', 'credential',
                  'transcripts', 'url', 'sha256'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--claude-transcripts')
    parser.add_argument('--deferred-environment')
    parser.add_argument('--install-agent', action='store_true',
                        help='Install the complete pinned package for the selected agent')
    print(render(**vars(parser.parse_args())))


if __name__ == '__main__':
    main()
