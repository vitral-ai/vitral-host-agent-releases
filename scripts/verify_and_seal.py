#!/usr/bin/env python3
"""Verify an explicitly authorized draft; publish once; verify immutable readback.

No release/tag/asset creation, signing, credential minting, or settings writes.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = "vitral-ai/vitral-host-agent-releases"
REPO_ID = 1463746626
SOURCE = "vitral-ai/vitralai"
WORKFLOW = ".github/workflows/host-agent-release.yml"
ISSUER = "https://token.actions.githubusercontent.com"
COSIGN_SHA = "424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa"
COSIGN_URL = "https://github.com/sigstore/cosign/releases/download/v3.1.4/cosign-linux-amd64"
BINARIES = tuple(f"vitral-host-agent-linux-{arch}" for arch in ("amd64", "arm64"))
LIMITS = {**{n: 128 * 1024**2 for n in BINARIES},
          **{n + ".sigstore.json": 1024**2 for n in BINARIES}, "SHA256SUMS": 8192}
SEMVER = (r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
          r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
          r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
          r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


class Rejected(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected), "invalid_fields")


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def parse_json(data):
    try:
        return json.loads(data, object_pairs_hook=no_duplicates)
    except (ValueError, UnicodeError) as exc:
        raise Rejected("invalid_json") from exc


def digest(data):
    return hashlib.sha256(data).hexdigest()


def authorization(data, tag):
    require(type(tag) is str and len(tag) <= 160 and
            re.fullmatch("host-agent-v" + SEMVER, tag), "invalid_tag")
    a = parse_json(data)
    fields(a, ("schema_version", "repository", "repository_id", "release_id", "tag", "public_target_sha",
               "source_sha", "qualified_receipt_sha256", "assets"))
    require(type(a["schema_version"]) is int and a["schema_version"] == 1, "invalid_schema")
    require(a["repository"] == REPO and type(a["repository_id"]) is int and
            a["repository_id"] == REPO_ID and a["tag"] == tag, "authorization_target_mismatch")
    positive_id(a["release_id"])
    for key, length in (("source_sha", 40), ("public_target_sha", 40),
                        ("qualified_receipt_sha256", 64)):
        require(type(a[key]) is str and re.fullmatch("[0-9a-f]{%d}" % length, a[key]),
                "invalid_approved_digest")
        require(len(set(a[key])) > 1, "placeholder_digest")
    fields(a["assets"], LIMITS)
    for name, spec in a["assets"].items():
        fields(spec, ("size", "sha256"))
        require(type(spec["size"]) is int and 0 < spec["size"] <= LIMITS[name], "invalid_asset_size")
        require(type(spec["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", spec["sha256"]),
                "invalid_asset_digest")
    return a


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token):
        require(bool(token), "missing_repository_token")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, url, *, accept="application/vnd.github+json", method="GET", body=None,
                limit=2 * 1024**2, authenticated=True, redirect_asset=False):
        parsed = urllib.parse.urlsplit(url)
        allowed = ("api.github.com",) if authenticated else ("release-assets.githubusercontent.com", "github.com")
        require(parsed.scheme == "https" and parsed.hostname in allowed and
                parsed.port in (None, 443) and not parsed.username and not parsed.password,
                "untrusted_download_origin")
        headers = {"Accept": accept, "User-Agent": "vitral-release-verifier",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, headers=headers, method=method,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(request, timeout=60) as response:
                result = response.read(limit + 1)
                require(len(result) <= limit, "response_too_large")
                return result
        except urllib.error.HTTPError as exc:
            if redirect_asset and method == "GET" and exc.code == 302:
                location = exc.headers.get("Location", "")
                exc.close()
                require(urllib.parse.urlsplit(location).hostname == "release-assets.githubusercontent.com",
                        "untrusted_asset_redirect")
                return self.request(location, accept="application/octet-stream", limit=limit,
                                    authenticated=False)
            code = exc.code
            exc.close()
            raise Rejected("github_http_%d" % code) from None
        except (OSError, ValueError) as exc:
            raise Rejected("github_transport_failure") from None

    def api(self, path, **kwargs):
        require((path == "" or path.startswith("/")) and ".." not in path and "?" not in path, "invalid_api_path")
        return parse_json(self.request("https://api.github.com/repos/" + REPO + path, **kwargs))

    def blob(self, asset_id, size):
        return self.request(f"https://api.github.com/repos/{REPO}/releases/assets/{asset_id}",
                            accept="application/octet-stream", limit=size, redirect_asset=True)


def positive_id(value):
    require(type(value) is int and value > 0, "invalid_github_id")
    return value


def snapshot(api, a):
    repo = api.api("")
    require(repo.get("id") == REPO_ID and repo.get("full_name") == REPO and repo.get("private") is False,
            "repository_identity_mismatch")
    tag = urllib.parse.quote(a["tag"], safe="")
    ref = api.api("/git/ref/tags/" + tag)
    require(ref.get("ref") == "refs/tags/" + a["tag"] and
            ref.get("object", {}).get("type") == "commit" and
            ref["object"].get("sha") == a["public_target_sha"], "public_tag_target_mismatch")
    # The by-tag endpoint is documented for published releases, not drafts.
    # Bind one real staged release ID in reviewed authorization for all states.
    r = api.api(f"/releases/{a['release_id']}")
    positive_id(r.get("id"))
    require(r["id"] == a["release_id"], "release_identity_mismatch")
    require(r.get("tag_name") == a["tag"] and r.get("name") == a["tag"] and
            r.get("body") == "Verified host-agent binaries for " + a["tag"] + "." and
            r.get("prerelease") is ("-" in a["tag"][12:].split("+")[0]), "release_metadata_mismatch")
    require(type(r.get("draft")) is bool and type(r.get("immutable")) is bool, "missing_release_state")
    if r["draft"]:
        require(r["immutable"] is False, "invalid_draft_state")
    else:
        require(r["immutable"] is True, "published_release_not_immutable")
    # Default first page holds 30 entries, already above the permitted five.
    # Any later page implies a full first page and is therefore rejected too.
    assets = api.api(f"/releases/{r['id']}/assets")
    require(type(assets) is list and len(assets) == len(LIMITS), "unexpected_asset_count")
    names, ids, fingerprint = set(), set(), []
    for item in assets:
        require(type(item) is dict and item.get("name") in LIMITS, "unexpected_asset_name")
        name = item["name"]
        asset_id = positive_id(item.get("id"))
        require(name not in names and asset_id not in ids, "duplicate_asset")
        names.add(name)
        ids.add(asset_id)
        expected = a["assets"][name]
        require(item.get("state") == "uploaded" and type(item.get("size")) is int and
                item["size"] == expected["size"] and
                item.get("digest") == "sha256:" + expected["sha256"], "asset_metadata_mismatch")
        fingerprint.append((name, asset_id, item["size"], item["digest"], item.get("updated_at")))
    require(names == set(LIMITS), "unexpected_asset_set")
    stable = {key: r[key] for key in ("id", "tag_name", "name", "body", "prerelease", "draft", "immutable")}
    return stable, tuple(sorted(fingerprint))


def cosign_args(cosign, binary, bundle, a):
    ref = "refs/tags/" + a["tag"]
    return [str(cosign), "verify-blob", "--bundle", str(bundle),
            "--certificate-identity", f"https://github.com/{SOURCE}/{WORKFLOW}@{ref}",
            "--certificate-oidc-issuer", ISSUER,
            "--certificate-github-workflow-repository", SOURCE,
            "--certificate-github-workflow-ref", ref,
            "--certificate-github-workflow-sha", a["source_sha"],
            "--certificate-github-workflow-trigger", "push", str(binary)]


def verify_cosign(cosign, binary, bundle, a, home):
    # Do not pass any Actions/GitHub/runtime credential to the verifier subprocess.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home)}
    try:
        result = subprocess.run(cosign_args(cosign, binary, bundle, a), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        raise Rejected("signature_verifier_failed") from None
    require(result.returncode == 0, "signature_verification_failed")


def verify_bytes(api, a, fingerprint, cosign, verifier=verify_cosign):
    with tempfile.TemporaryDirectory(prefix="vitral-release-verify-") as tmp:
        home = Path(tmp)
        content = {}
        for name, asset_id, size, _, _ in fingerprint:
            data = api.blob(asset_id, size)
            require(len(data) == size and digest(data) == a["assets"][name]["sha256"],
                    "asset_bytes_mismatch")
            (home / name).write_bytes(data)
            if name == "SHA256SUMS":
                content[name] = data
        expected = "".join(a["assets"][name]["sha256"] + "  " + name + "\n"
                           for name in sorted(set(LIMITS) - {"SHA256SUMS"})).encode()
        require(content["SHA256SUMS"] == expected, "checksums_manifest_mismatch")
        for name in BINARIES:
            verifier(cosign, home / name, home / (name + ".sigstore.json"), a, home)


def seal(api, a, cosign, verifier=verify_cosign, before_publish=None):
    release, before = snapshot(api, a)
    verify_bytes(api, a, before, cosign, verifier)
    latest, immediately_before = snapshot(api, a)
    require(latest == release and immediately_before == before, "draft_changed_during_verification")
    if release["draft"]:
        if before_publish:
            before_publish()
        try:
            # Exactly one write. A failed response may still have committed; never retry here.
            api.api(f"/releases/{release['id']}", method="PATCH", body={"draft": False})
        except Exception:
            raise Rejected("seal_outcome_unknown_readback_required") from None
    after, frozen = snapshot(api, a)
    require(after["id"] == release["id"] and after["draft"] is False and after["immutable"] is True
            and frozen == before, "immutable_readback_mismatch")
    verify_bytes(api, a, frozen, cosign, verifier)
    return {"status": "verified_immutable", "repository_id": REPO_ID,
            "release_id": after["id"], "tag": a["tag"], "source_sha": a["source_sha"]}


def preflight(value, now=None):
    try:
        timestamp = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        raise Rejected("invalid_operator_preflight_time") from None
    age = ((now or dt.datetime.now(dt.timezone.utc)) - timestamp).total_seconds()
    require(0 <= age <= 900, "stale_operator_preflight")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--immutable-preflight-utc", required=True)
    parser.add_argument("--exclusive-staging-owner", required=True, choices=["confirmed"])
    args = parser.parse_args()
    # Validate tag before constructing any filesystem path or making API requests.
    require(re.fullmatch("host-agent-v" + SEMVER, args.tag or "") and len(args.tag) <= 160, "invalid_tag")
    path = Path(__file__).resolve().parent.parent / "release-authorizations" / (args.tag + ".json")
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            "reviewed_authorization_missing")
    a = authorization(path.read_bytes(), args.tag)
    require(os.environ.get("GITHUB_REPOSITORY") == REPO and
            os.environ.get("GITHUB_REPOSITORY_ID") == str(REPO_ID) and
            os.environ.get("GITHUB_REF") == "refs/heads/main" and
            os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "invalid_execution_context")
    preflight(args.immutable_preflight_utc)
    api = GitHub(os.environ.get("GH_TOKEN"))
    with tempfile.TemporaryDirectory(prefix="vitral-cosign-") as tmp:
        tool = Path(tmp) / "cosign"
        data = api.request(COSIGN_URL, authenticated=False, accept="application/octet-stream",
                           limit=256 * 1024**2, redirect_asset=True)
        require(digest(data) == COSIGN_SHA, "cosign_digest_mismatch")
        tool.write_bytes(data)
        tool.chmod(0o700)
        print(json.dumps(seal(api, a, tool,
                              before_publish=lambda: preflight(args.immutable_preflight_utc)), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Rejected, OSError) as exc:
        print("verification_failed: " + (str(exc) if isinstance(exc, Rejected) else "local_io_failure"),
              file=sys.stderr)
        sys.exit(1)
