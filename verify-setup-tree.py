"""Verify retained setup dependencies against a freshly verified extraction."""

from hashlib import file_digest
from pathlib import Path
import os
import re
import stat
import sys


def walk_error(error):
    raise error


def scan(root):
    entries = {}
    for directory, subdirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in (*subdirs, *files):
            path = Path(directory) / name
            info = path.lstat()
            mode = info.st_mode
            if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                if not stat.S_ISLNK(mode):
                    raise ValueError("unexpected Windows reparse point")
            key = path.relative_to(root)
            if stat.S_ISLNK(mode):
                entries[key] = ("link", os.readlink(path))
                if name in subdirs:
                    subdirs.remove(name)
            elif stat.S_ISDIR(mode):
                entries[key] = ("directory",)
            elif stat.S_ISREG(mode):
                with path.open("rb") as stream:
                    entries[key] = ("file", file_digest(stream, "sha256").hexdigest())
            else:
                raise ValueError("unexpected file type")
    return entries


def verified_cache_extras(expected, actual):
    extras = actual.keys() - expected.keys()
    bytecode = []
    directories = []
    for path in extras:
        if (path.name == "__pycache__" and actual[path] == ("directory",)
                and expected.get(path.parent, ("directory",) if str(path.parent) == "." else None)
                == ("directory",)):
            directories.append(path)
        elif (path.parent.name == "__pycache__" and actual[path][0] == "file"
              and re.fullmatch(r"[^/]+\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc", path.name)
              and (expected.get(path.parent) == ("directory",) or path.parent in extras)):
            bytecode.append(path)
        else:
            raise ValueError("unexpected retained file")
    return bytecode, directories


def verify(reference, retained):
    expected, actual = scan(reference), scan(retained)
    if any(actual.get(path) != entry for path, entry in expected.items()):
        raise ValueError("retained content differs from its verified archive")
    bytecode, directories = verified_cache_extras(expected, actual)
    # Python may create bytecode between installs. Remove only unverified
    # generated caches before invoking any retained interpreter or source.
    for item in bytecode:
        (retained / item).unlink()
    for item in directories:
        (retained / item).rmdir()


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError):
        sys.exit("Retained setup dependency failed verification. Choose a clean setup data directory.")
