"""Verify a distribution draft and attach its keyless bundle."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPO = "vitral-ai/vitral-host-agent-releases"
REPO_ID = "1463746626"
WORKFLOW = ".github/workflows/sign-host-release.yml"
PREFIX = "host-agent-publish-v"
PRE = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
STABLE_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
SEMVER = rf"{STABLE_VERSION}(?:-{PRE}(?:\.{PRE})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
ISSUER = "https://token.actions.githubusercontent.com"
BINARIES = (
    "vitral-host-darwin-amd64",
    "vitral-host-darwin-arm64",
    "vitral-host-linux-amd64",
    "vitral-host-linux-arm64",
    "vitral-host-windows-amd64.exe",
    "vitral-host-windows-arm64.exe",
)
SUMS, BUNDLE = "SHA256SUMS", "SHA256SUMS.sigstore.json"
METADATA_LIMITS = {SUMS: 8192, BUNDLE: 1 << 20}
PAYLOAD_LIMIT = 128 << 20
COSIGN_VERSION = "3.1.4"
COSIGN_SHA256 = "424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa"
BASE = f"repos/{REPO}"


@dataclass(frozen=True)
class ReleaseProfile:
    prefix: str
    workflow: str
    payloads: tuple[str, ...]
    production_gate: str
    production_version: str = SEMVER


HOST_PROFILE = ReleaseProfile(PREFIX, WORKFLOW, BINARIES, "VITRAL_PRODUCTION_SIGNING_ENABLED")


def require(value, reason):
    if not value:
        raise ValueError(reason)


def context(env, profile=HOST_PROFILE):
    ref = env.get("GITHUB_REF", "")
    tag = ref.removeprefix("refs/tags/")
    version = tag.removeprefix(profile.prefix)
    staging = re.fullmatch(r"0\.0\.0-dryrun\.(?:0|[1-9][0-9]*)", version) is not None
    require(env.get("GITHUB_REPOSITORY") == REPO and env.get("GITHUB_REPOSITORY_ID") == REPO_ID
            and env.get("GITHUB_EVENT_NAME") in {"push", "workflow_dispatch"}
            and env.get("GITHUB_REF_TYPE") == "tag" and ref.startswith("refs/tags/")
            and tag.startswith(profile.prefix) and len(version) <= 128
            and (staging or re.fullmatch(profile.production_version, version))
            and env.get("GITHUB_WORKFLOW_REF") == f"{REPO}/{profile.workflow}@{ref}"
            and env.get("GITHUB_WORKFLOW_SHA") == env.get("GITHUB_SHA")
            and re.fullmatch("[0-9a-f]{40}", env.get("GITHUB_SHA", "")), "public signing context is invalid")
    if staging:
        return tag, True
    require(env.get(profile.production_gate) == "true", "production signing requires explicit approval")
    return tag, False


def gh(*args, output=None):
    command = ["gh", "api", "--hostname", "github.com", *args]
    if output is None:
        return json.loads(subprocess.check_output(command, timeout=180))
    with output.open("xb") as stream:
        subprocess.run(command, stdout=stream, timeout=180, check=True)


def get_draft(tag, api=gh):
    pages = api("--paginate", "--slurp", f"{BASE}/releases?per_page=100")
    matches = [item for page in pages for item in page if item.get("tag_name") == tag]
    require(len(matches) == 1, "expected exactly one release for the signing tag")
    release = matches[0]
    require(type(release.get("id")) is int and release.get("draft") is True,
            "the matching release must be a draft")
    return release


def asset_records(release, *, allow_bundle=True, profile=HOST_PROFILE):
    assets = release["assets"]
    result = {asset["name"]: asset for asset in assets}
    allowed = {SUMS, *profile.payloads}
    valid_sets = (allowed, allowed | {BUNDLE}) if allow_bundle else (allowed,)
    require(len(result) == len(assets) and set(result) in valid_sets, "unexpected draft asset set")
    for name, asset in result.items():
        require(type(asset["id"]) is int and asset["state"] == "uploaded"
                and type(asset["size"]) is int and 0 < asset["size"] <= METADATA_LIMITS.get(name, PAYLOAD_LIMIT)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest", "")), "invalid draft asset metadata")
    return {name: {key: asset[key] for key in ("id", "size", "digest")} for name, asset in result.items()}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_assets(assets, output, api=gh, *, profile=HOST_PROFILE):
    for name, asset in assets.items():
        path = output / name
        api(f"{BASE}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream", output=path)
        require(path.stat().st_size == asset["size"] and "sha256:" + digest(path) == asset["digest"],
                f"downloaded bytes differ for {name}")
    expected = "".join(f"{digest(output / name)}  {name}\n" for name in profile.payloads).encode()
    require((output / SUMS).read_bytes() == expected, "checksum manifest differs from the draft payloads")


def obtain_cosign(path, run=subprocess.run):
    run(["curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https", "--proto-redir", "=https",
         "--max-filesize", str(256 << 20), "--max-time", "180", "--output", str(path),
         f"https://github.com/sigstore/cosign/releases/download/v{COSIGN_VERSION}/cosign-linux-amd64"],
        check=True, timeout=190)
    require(digest(path) == COSIGN_SHA256, "Cosign pin mismatch")
    path.chmod(0o700)


def sign_and_verify(path, tag, staging, output, *, run=subprocess.run, profile=HOST_PROFILE):
    env = {key: os.environ[key] for key in ("PATH", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL")
           if key in os.environ}
    require(all(env.get(key) for key in ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL")),
            "missing GitHub Actions OIDC context")
    env["TUF_ROOT"] = str(output / "tuf")
    sign_opts, verify_opts = [], []
    if staging:
        run([str(path), "initialize", "--staging"], env=env, check=True, timeout=180)
        targets = Path(env["TUF_ROOT"]) / "tuf-repo-cdn.sigstage.dev" / "targets"
        config, root = targets / "signing_config.v0.2.json", targets / "trusted_root.json"
        endpoints = json.loads(config.read_text())
        for group in ("caUrls", "rekorTlogUrls", "tsaUrls", "oidcUrls"):
            require(endpoints.get(group) and all(
                re.fullmatch(r"https://[a-z0-9.-]+\.sigstage\.dev(?:/[^\s]*)?", item["url"])
                for item in endpoints[group]), "non-staging endpoint in signing configuration")
        sign_opts += ["--signing-config", str(config), "--trusted-root", str(root)]
        verify_opts += ["--trusted-root", str(root), "--use-signed-timestamps"]
    if not (output / BUNDLE).exists():
        run([str(path), "sign-blob", "--yes", *sign_opts, "--bundle", str(output / BUNDLE), str(output / SUMS)],
            env=env, check=True, timeout=180)
    run([str(path), "verify-blob", *verify_opts, "--bundle", str(output / BUNDLE),
         "--certificate-identity", f"https://github.com/{REPO}/{profile.workflow}@refs/tags/{tag}",
         "--certificate-oidc-issuer", ISSUER, str(output / SUMS)], env={"PATH": env["PATH"], "TUF_ROOT": env["TUF_ROOT"]},
        check=True, timeout=180)


def sign_draft(env, output, *, api=gh, acquire=obtain_cosign, sign=None, profile=HOST_PROFILE):
    tag, staging = context(env, profile)
    draft = get_draft(tag, api)
    assets = asset_records(draft, profile=profile)
    verify_assets(assets, output, api, profile=profile)
    cosign = output / "cosign"
    acquire(cosign)
    if sign is None:
        sign_and_verify(cosign, tag, staging, output, profile=profile)
    else:
        sign(cosign, tag, staging, output)
    # Recheck before upload in case the draft was modified during signing.
    current = get_draft(tag, api)
    require(current["id"] == draft["id"] and asset_records(current, profile=profile) == assets,
            "draft changed during signing")
    if BUNDLE not in assets:
        bundle = output / BUNDLE
        size, bundle_digest = bundle.stat().st_size, "sha256:" + digest(bundle)
        require(0 < size <= METADATA_LIMITS[BUNDLE], "invalid generated bundle size")
        api("--method", "POST", f"https://uploads.github.com/{BASE}/releases/{draft['id']}/assets?name={BUNDLE}",
            "-H", "Content-Type: application/octet-stream", "--input", str(bundle))
        current = get_draft(tag, api)
        after = asset_records(current, profile=profile)
        require(current["id"] == draft["id"] and (after[BUNDLE]["size"], after[BUNDLE]["digest"]) == (size, bundle_digest)
                and {name: asset for name, asset in after.items() if name != BUNDLE} == assets,
                "signed draft readback differs")
    return {"release_id": draft["id"], "tag": tag, "sigstore": "staging" if staging else "production", "draft": True}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="vitral-sign-") as directory:
        print(json.dumps(sign_draft(os.environ, Path(directory)), sort_keys=True))
