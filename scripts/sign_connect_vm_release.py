"""Sign the reviewed Connect VM distribution draft."""

import json
import os
from pathlib import Path
import tempfile

from sign_host_release import ReleaseProfile, sign_draft


CONNECT_VM_PROFILE = ReleaseProfile(
    "connect-vm-publish-v",
    ".github/workflows/sign-connect-vm-release.yml",
    ("vitral-connect-linux-amd64.tar", "vitral-connect-linux-arm64.tar"),
    "VITRAL_CONNECT_VM_PRODUCTION_SIGNING_ENABLED",
)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="vitral-connect-vm-sign-") as directory:
        print(json.dumps(sign_draft(os.environ, Path(directory), profile=CONNECT_VM_PROFILE), sort_keys=True))
