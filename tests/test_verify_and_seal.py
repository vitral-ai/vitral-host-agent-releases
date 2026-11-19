import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import io
import sys

spec = importlib.util.spec_from_file_location("seal", Path(__file__).parents[1] / "scripts/verify_and_seal.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TAG = "host-agent-v1.2.3-rc.1"


def fixture():
    blobs = {n: ("synthetic-test-only-" + n).encode() for n in m.LIMITS if n != "SHA256SUMS"}
    blobs["SHA256SUMS"] = "".join(m.digest(blobs[n]) + "  " + n + "\n" for n in sorted(blobs)).encode()
    a = {"schema_version": 1, "repository": m.REPO, "repository_id": m.REPO_ID, "tag": TAG,
         "public_target_sha": "ab" * 20, "source_sha": "cd" * 20,
         "qualified_receipt_sha256": "ef" * 32,
         "assets": {n: {"size": len(b), "sha256": m.digest(b)} for n, b in blobs.items()}}
    return a, blobs


class FakeAPI:
    def __init__(self):
        self.a, self.blobs = fixture()
        self.assets = [{"id": i, "name": n, "size": len(b), "digest": "sha256:" + m.digest(b),
                        "state": "uploaded", "updated_at": "2026-11-19T00:00:00Z"}
                       for i, (n, b) in enumerate(self.blobs.items(), 1)]
        self.release = {"id": 42, "tag_name": TAG, "name": TAG,
                        "body": "Verified host-agent binaries for " + TAG + ".",
                        "prerelease": True, "draft": True, "immutable": False}
        self.writes = []
        self.reads = 0
        self.downloads = 0
        self.change_at = None
        self.publish_immutable = True
        self.uncertain = False

    def api(self, path, **kwargs):
        if kwargs.get("method") == "PATCH":
            self.writes.append((path, kwargs))
            self.release.update(draft=False, immutable=self.publish_immutable)
            if self.uncertain:
                raise m.Rejected("github_transport_failure")
            return copy.deepcopy(self.release)
        if path == "":
            self.reads += 1
            if self.change_at == self.reads:
                self.assets[0]["id"] += 100
            return {"id": m.REPO_ID, "full_name": m.REPO, "private": False}
        if path.startswith("/git/ref/tags/"):
            return {"ref": "refs/tags/" + TAG, "object": {"type": "commit", "sha": self.a["public_target_sha"]}}
        if path.startswith("/releases/tags/"):
            return copy.deepcopy(self.release)
        if path == "/releases/42/assets":
            return copy.deepcopy(self.assets)
        raise AssertionError(path)

    def blob(self, asset_id, size):
        self.downloads += 1
        asset = next(x for x in self.assets if x["id"] == asset_id)
        return self.blobs[asset["name"]]


class SealTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.verified = []

    def verifier(self, tool, binary, bundle, a, home):
        self.verified.append((binary.name, bundle.name, a["source_sha"]))

    def run_seal(self):
        return m.seal(self.api, self.api.a, Path("unused-test-tool"), self.verifier)

    def rejected(self, code):
        return self.assertRaisesRegex(m.Rejected, "^" + code + "$")

    def test_exact_authorization(self):
        self.assertEqual(m.authorization(json.dumps(self.api.a), TAG), self.api.a)

    def test_path_and_semver_rejected(self):
        for tag in ("../x", "host-agent-v01.2.3", "host-agent-v1.2.3-01", TAG + "/x"):
            with self.subTest(tag=tag), self.rejected("invalid_tag"):
                m.authorization("{}", tag)

    def test_duplicate_or_unknown_json_fields_rejected(self):
        with self.rejected("duplicate_json_key"):
            m.authorization('{"tag":"a","tag":"b"}', TAG)
        self.api.a["signing_identity_override"] = "public-repo"
        with self.rejected("invalid_fields"):
            m.authorization(json.dumps(self.api.a), TAG)

    def test_wrong_repository_and_boolean_id_rejected(self):
        for key, value in (("repository", "other/repo"), ("repository_id", True)):
            a = copy.deepcopy(self.api.a)
            a[key] = value
            with self.rejected("authorization_target_mismatch"):
                m.authorization(json.dumps(a), TAG)

    def test_missing_extra_or_oversize_authorization_assets_rejected(self):
        for mutation in ("missing", "extra", "oversize"):
            a = copy.deepcopy(self.api.a)
            if mutation == "missing":
                del a["assets"]["SHA256SUMS"]
            elif mutation == "extra":
                a["assets"]["source.zip"] = {"size": 1, "sha256": "a" * 64}
            else:
                a["assets"]["SHA256SUMS"]["size"] = 8193
            with self.subTest(mutation=mutation), self.assertRaises(m.Rejected):
                m.authorization(json.dumps(a), TAG)

    def test_success_single_write_and_full_readback(self):
        self.assertEqual(self.run_seal()["status"], "verified_immutable")
        self.assertEqual(self.api.writes, [("/releases/42", {"method": "PATCH", "body": {"draft": False}})])
        self.assertEqual(self.api.downloads, 10)
        self.assertEqual(len(self.verified), 4)

    def test_already_immutable_is_readonly(self):
        self.api.release.update(draft=False, immutable=True)
        self.run_seal()
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.api.downloads, 10)

    def test_duplicate_extra_missing_or_unuploaded_assets_block_write(self):
        for mutation in ("duplicate", "extra", "missing", "state", "size", "digest"):
            with self.subTest(mutation=mutation):
                self.api = FakeAPI()
                if mutation == "duplicate":
                    self.api.assets[0] = copy.deepcopy(self.api.assets[1])
                elif mutation == "extra":
                    self.api.assets.append(copy.deepcopy(self.api.assets[0]))
                elif mutation == "missing":
                    self.api.assets.pop()
                else:
                    self.api.assets[0][mutation] = {"state": "new", "size": 0, "digest": None}[mutation]
                with self.assertRaises(m.Rejected):
                    self.run_seal()
                self.assertEqual(self.api.writes, [])

    def test_digest_mismatch_blocks_write(self):
        self.api.blobs[m.BINARIES[0]] += b"changed"
        with self.rejected("asset_bytes_mismatch"):
            self.run_seal()
        self.assertEqual(self.api.writes, [])

    def test_canonical_checksums_required(self):
        self.api.blobs["SHA256SUMS"] = b"wrong but independently approved hash\n"
        data = self.api.blobs["SHA256SUMS"]
        self.api.a["assets"]["SHA256SUMS"] = {"size": len(data), "sha256": m.digest(data)}
        self.api.assets[-1].update(size=len(data), digest="sha256:" + m.digest(data))
        with self.rejected("checksums_manifest_mismatch"):
            self.run_seal()
        self.assertEqual(self.api.writes, [])

    def test_signature_failure_blocks_write(self):
        def fail(*args):
            raise m.Rejected("signature_verification_failed")
        with self.rejected("signature_verification_failed"):
            m.seal(self.api, self.api.a, "unused", fail)
        self.assertEqual(self.api.writes, [])

    def test_asset_replacement_before_publish_blocks_write(self):
        self.api.change_at = 2
        with self.rejected("draft_changed_during_verification"):
            self.run_seal()
        self.assertEqual(self.api.writes, [])

    def test_not_immutable_after_publish_never_passes(self):
        self.api.publish_immutable = False
        with self.rejected("published_release_not_immutable"):
            self.run_seal()
        self.assertEqual(len(self.api.writes), 1)

    def test_uncertain_write_not_retried_and_rerun_reads_only(self):
        self.api.uncertain = True
        with self.rejected("seal_outcome_unknown_readback_required"):
            self.run_seal()
        self.assertEqual(len(self.api.writes), 1)
        self.run_seal()
        self.assertEqual(len(self.api.writes), 1)

    def test_changed_asset_after_publish_never_passes(self):
        self.api.change_at = 3
        with self.rejected("immutable_readback_mismatch"):
            self.run_seal()
        self.assertEqual(len(self.api.writes), 1)

    def test_published_byte_change_never_passes(self):
        original = self.api.blob
        def blob(asset_id, size):
            data = original(asset_id, size)
            return data + b"changed" if self.api.downloads == 6 else data
        self.api.blob = blob
        with self.rejected("asset_bytes_mismatch"):
            self.run_seal()
        self.assertEqual(len(self.api.writes), 1)

    def test_stale_preflight_after_verification_prevents_write(self):
        def stale():
            raise m.Rejected("stale_operator_preflight")
        with self.rejected("stale_operator_preflight"):
            m.seal(self.api, self.api.a, "unused", self.verifier, stale)
        self.assertEqual(self.api.writes, [])

    def test_missing_authorization_fails_before_network(self):
        argv = ["verify_and_seal.py", "--tag", "host-agent-v999.999.999",
                "--immutable-preflight-utc", "2026-11-19T00:00:00Z",
                "--exclusive-staging-owner", "confirmed"]
        with patch.object(sys, "argv", argv), patch.object(m, "GitHub") as api:
            with self.rejected("reviewed_authorization_missing"):
                m.main()
            api.assert_not_called()

    def test_release_metadata_and_annotated_tag_rejected(self):
        self.api.release["body"] += "unexpected"
        with self.rejected("release_metadata_mismatch"):
            self.run_seal()
        self.assertEqual(self.api.writes, [])
        self.api = FakeAPI()
        original = self.api.api
        def api(path, **kwargs):
            data = original(path, **kwargs)
            if path.startswith("/git/ref/tags/"):
                data["object"]["type"] = "tag"
            return data
        self.api.api = api
        with self.rejected("public_tag_target_mismatch"):
            self.run_seal()
        self.assertEqual(self.api.writes, [])

    def test_exact_private_source_certificate_flags_and_no_credentials(self):
        args = m.cosign_args("cosign", "binary", "bundle", self.api.a)
        expected = {"--certificate-identity": f"https://github.com/{m.SOURCE}/{m.WORKFLOW}@refs/tags/{TAG}",
                    "--certificate-oidc-issuer": m.ISSUER,
                    "--certificate-github-workflow-repository": m.SOURCE,
                    "--certificate-github-workflow-ref": "refs/tags/" + TAG,
                    "--certificate-github-workflow-sha": self.api.a["source_sha"],
                    "--certificate-github-workflow-trigger": "push"}
        for flag, value in expected.items():
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertFalse(any("insecure" in x or "regexp" in x for x in args))
        with patch.object(m.subprocess, "run") as run:
            run.return_value.returncode = 0
            m.verify_cosign("cosign", Path("binary"), Path("bundle"), self.api.a, Path("/tmp/test"))
            self.assertEqual(set(run.call_args.kwargs["env"]), {"PATH", "HOME", "TMPDIR"})

    def test_operator_preflight_freshness(self):
        now = dt.datetime(2026, 11, 19, 7, tzinfo=dt.timezone.utc)
        m.preflight("2026-11-19T06:59:00Z", now)
        for value in ("2026-11-19T06:44:59Z", "2026-11-19T07:00:01Z", "invalid"):
            with self.assertRaises(m.Rejected):
                m.preflight(value, now)

    def test_foreign_api_origin_rejected_before_request(self):
        api = m.GitHub("synthetic-test-value")
        with self.rejected("untrusted_download_origin"):
            api.request("https://example.com/private", authenticated=True)

    def test_redirect_never_forwards_token(self):
        api = m.GitHub("synthetic-test-value")
        requests = []
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        def open_request(req, timeout):
            requests.append(req)
            if len(requests) == 1:
                raise urllib.error.HTTPError(req.full_url, 302, "redirect", {
                    "Location": "https://release-assets.githubusercontent.com/asset?test=1"}, None)
            return Response(b"ok")
        with patch.object(api.opener, "open", side_effect=open_request):
            self.assertEqual(api.blob(1, 2), b"ok")
        self.assertIsNotNone(requests[0].get_header("Authorization"))
        self.assertIsNone(requests[1].get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
