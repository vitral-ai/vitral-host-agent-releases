"""Complete prepared VM enrollment over an authenticated operator connection."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from uuid import UUID


ROOT = Path('/var/lib/vitral-connect-bootstrap')
COMPLETE = Path('/var/lib/vitral-connect/enrollment-complete')
ENROLL = 'vitral-connect-enroll.service'
SERVICES = ('vitral-connect-host.service', 'vitral-connect-collector.service')
WRAPPER = 'vitral-connect-wrapper.service'


def scope(organization, environment):
    if not re.fullmatch(r'[0-9a-f]{24}', organization) or str(UUID(environment)) != environment:
        raise ValueError('Invalid reserved environment scope')
    return {'organization_id': organization, 'environment_id': environment, 'generation': 1}


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def locked(root, *, create=False):
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Invalid managed enrollment directory')
    fd = os.open(root / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def prepare(root, expected):
    with locked(root, create=True):
        path = root / 'scope.json'
        if path.exists():
            if json.loads(path.read_bytes()) != expected:
                raise ValueError('The prepared guest belongs to another reservation')
        else:
            atomic_write(path, json.dumps(expected, sort_keys=True).encode())


def service(*arguments, check=True):
    return subprocess.run(['/usr/bin/systemctl', *arguments], check=check,
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=180).returncode == 0


def verify_receipt(complete, expected):
    if complete.is_symlink() or not complete.is_file():
        raise ValueError('The enrollment completion marker is unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    base = os.open(complete.parent, flags)
    try:
        host = os.open('host', flags, dir_fd=base)
        try:
            descriptor = os.open('connect.json', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=host)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                owner = os.fstat(base).st_uid
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != owner or os.fstat(host).st_uid != owner or info.st_size > 65536):
                    raise ValueError('The enrollment receipt is not a private regular file')
                raw = stream.read(65537)
        finally:
            os.close(host)
    finally:
        os.close(base)
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise ValueError('Invalid committed enrollment receipt')
    assignment = receipt.get('assignment')
    if (len(raw) > 65536 or receipt.get('complete') is not True or receipt.get('reservation') != expected
            or not isinstance(assignment, dict) or type(assignment.get('generation')) is not int
            or any(assignment.get(key) != expected[key] for key in ('environment_id', 'generation'))):
        raise ValueError('The committed receipt differs from the guest reservation')


def status(root, complete, expected, *, run=service):
    if complete.is_file():
        verify_receipt(complete, expected)
    elif (root / 'ready').is_file():
        raise ValueError('The ready guest has no enrollment receipt')
    if (root / 'ready').is_file() and all(run('is-active', '--quiet', unit, check=False) for unit in SERVICES):
        return 'ready'
    if (root / 'token').is_file() or complete.is_file():
        return 'resume'
    return 'needs_token'


def activate(root, complete, expected, credential, *, run=service):
    if credential and not re.fullmatch(rb'[A-Za-z0-9_-]{43}', credential):
        raise ValueError('Invalid reserved join token encoding')
    phase = status(root, complete, expected, run=run)
    if phase == 'ready':
        if credential:
            raise ValueError('The prepared guest is already enrolled')
        return phase
    if not complete.is_file():
        if credential:
            atomic_write(root / 'token', credential)
        if not (root / 'token').is_file():
            raise ValueError('The reserved guest needs an enrollment credential')
        run('start', ENROLL)
    verify_receipt(complete, expected)
    (root / 'token').unlink(missing_ok=True)
    for unit in SERVICES:
        run('enable', '--now', unit)
    run('start', WRAPPER)
    if not all(run('is-active', '--quiet', unit, check=False) for unit in SERVICES):
        raise ValueError('The Vitral guest services are unavailable')
    atomic_write(root / 'ready', b'1')
    return 'ready'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('organization')
    prep.add_argument('environment')
    commands.add_parser('status')
    commands.add_parser('activate')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(2, 'The managed guest handoff requires root.\n')
    try:
        if args.command == 'prepare':
            prepare(ROOT, scope(args.organization, args.environment))
            return
        if not (ROOT / 'scope.json').exists():
            if args.command != 'status':
                raise ValueError('The managed guest is still preparing')
            print('{"state":"preparing"}')
            return
        with locked(ROOT):
            expected = json.loads((ROOT / 'scope.json').read_bytes())
            if args.command == 'status':
                result = status(ROOT, COMPLETE, expected)
            else:
                result = activate(ROOT, COMPLETE, expected, sys.stdin.buffer.read(45).removesuffix(b'\n'))
            print(json.dumps({**expected, 'state': result}))
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, 'Managed guest handoff failed.\n')


if __name__ == '__main__':
    main()
