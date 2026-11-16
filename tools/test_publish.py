import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from build import build, engine_range, file_digest, json_bytes, read_descriptor
from publish import CATALOG_FILE, CATALOG_REF, RELEASE_MARKER, RELEASE_SUFFIX, GIT_AUTHOR, git, publish


class FakeGitHub:
    repository = "Albeoris/Memoria.Mods"

    def __init__(self, remote):
        self.remote = remote
        self.records = {}
        self.uploads = 0
        self.fail_upload = False
        self.fail_catalog_check = False

    def releases(self):
        return copy.deepcopy(list(self.records.values()))

    def create_draft(self, source, component, identity):
        tag = component["latest"]["releaseTag"]
        if tag in self.records:
            raise AssertionError(f"Duplicate draft: {tag}")
        release = {"id": tag, "tag_name": tag, "draft": True, "assets": [], "target_commitish": source,
                   "body": f"{RELEASE_MARKER}{json.dumps(identity)}{RELEASE_SUFFIX}\n"}
        self.records[tag] = release
        return copy.deepcopy(release)

    def upload(self, release, archive):
        self.uploads += 1
        size, sha256 = file_digest(archive)
        asset = {"name": archive.name, "state": "uploaded", "size": size, "digest": f"sha256:{sha256}"}
        self.records[release["id"]]["assets"].append(asset)
        if self.fail_upload:
            self.fail_upload = False
            raise RuntimeError("interrupted after upload")
        return asset

    def publish(self, release):
        self.records[release["id"]]["draft"] = False
        return copy.deepcopy(self.records[release["id"]])

    def delete_asset(self, asset):
        for record in self.records.values():
            if asset in record["assets"]:
                record["assets"].remove(asset)
                return
        raise AssertionError("Tried to delete an unknown asset")

    def delete_release(self, release):
        catalog = self.read_catalog()
        advertised = {entry["latest"]["releaseTag"] for entry in catalog["components"]}
        if release["id"] in advertised:
            raise AssertionError("Attempted to delete an advertised release")
        del self.records[release["id"]]

    def verify_artifact(self, artifact):
        tag = artifact["url"].split("/")[-2]
        release = self.records[tag]
        if release["draft"] or release["assets"][0]["digest"] != f"sha256:{artifact['sha256']}":
            raise AssertionError("Invalid anonymous artifact")

    def read_catalog(self):
        return json.loads(git(self.remote, "show", f"{CATALOG_REF}:{CATALOG_FILE}"))

    def verify_catalog(self, catalog):
        if self.fail_catalog_check:
            self.fail_catalog_check = False
            raise RuntimeError("interrupted after catalog switch")
        if self.read_catalog() != catalog:
            raise AssertionError("Unexpected catalog snapshot")


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote"
        git(self.root, "init", "-b", "main", "source")
        git(self.root, "init", "--bare", "remote")
        self.repository = self.root / "source"
        git(self.repository, "remote", "add", "origin", str(self.remote))
        self.host = FakeGitHub(self.remote)
        self.default_calls = 0

    def create_mod(self, component_id, *, minimum=None):
        folder = self.repository / "mods" / component_id
        payload = folder / "payload"
        payload.mkdir(parents=True)
        self.update_payload(component_id, b"original")
        (payload / "mod.ini").write_bytes(b"enabled = 1\r\n")
        descriptor = {
            "id": component_id, "groupId": "Memoria", "runtimePackageId": f"Memoria/{component_id}",
            "settingsKey": component_id, "name": component_id, "description": f"Description of {component_id}.",
            "category": "Items", "format": "memoria-native", "delivery": "download", "defaultEnabled": True,
            "target": {"game": "FFVIII", "editions": ["Classic"], "languages": ["*"]},
            "mutableFiles": ["mod.ini"],
        }
        if minimum:
            descriptor["engineVersionRange"] = minimum
        (folder / "mod.json").write_bytes(json_bytes(descriptor))
        return folder

    def update_payload(self, component_id, data):
        (self.repository / "mods" / component_id / "payload" / "image.png").write_bytes(data)

    def commit(self):
        git(self.repository, "add", ".")
        git(self.repository, *GIT_AUTHOR, "commit", "-m", "Update source")

    def resolve_minimum(self):
        self.default_calls += 1
        return "[1.2.3,)"

    def run_publication(self):
        publish(self.repository, self.host, self.resolve_minimum)

    def test_package_reproducibility_and_inventory(self):
        folder = self.create_mod("artwork")
        descriptor = read_descriptor(folder)
        first, component = build(folder, self.root / "first", descriptor, "1.0.0", "[1.0.0-beta.1,)")
        second, repeated = build(folder, self.root / "second", descriptor, "1.0.0", "[1.0.0-beta.1,)")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(component, repeated)
        with ZipFile(first) as archive:
            manifest = json.loads(archive.read("mod.manifest.json"))
            self.assertEqual(["image.png", "mod.ini"], [item["path"] for item in manifest["files"]])
            self.assertEqual([False, True], [item["mutable"] for item in manifest["files"]])
            self.assertEqual(b"enabled = 1\r\n", archive.read("payload/mod.ini"))
            self.assertEqual(sum(entry.file_size for entry in archive.infolist()), component["latest"]["artifact"]["unpackedSize"])

    def test_reject_bounded_or_invalid_engine_minimum(self):
        for invalid in ("1.2.3", "(1.2.3,)", "[1.2.3]", "[1.2.3,2.0.0]", "[,)", "[01.2.3,)"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                engine_range(invalid)

    def test_changes_advance_only_the_selected_component(self):
        self.create_mod("artwork", minimum="[0.8.10,)")
        self.create_mod("other")
        self.commit()
        self.run_publication()
        initial = self.host.read_catalog()
        self.assertEqual(2, initial["revision"])
        self.assertEqual(["[0.8.10,)", "[1.2.3,)"], [entry["latest"]["engineVersionRange"] for entry in initial["components"]])
        self.assertEqual(1, self.default_calls)

        self.update_payload("other", b"other new image")
        self.commit()
        self.run_publication()
        updated = self.host.read_catalog()
        self.assertEqual(3, updated["revision"])
        self.assertEqual(initial["components"][0], updated["components"][0])
        self.assertEqual("[1.2.3,)", updated["components"][1]["latest"]["engineVersionRange"])
        self.assertEqual({"artwork-v1.0.0", "other-v1.0.1"}, set(self.host.records))
        self.assertEqual(1, self.default_calls)
        (self.repository / "README.md").write_text("unrelated change", encoding="utf-8")
        self.commit()
        self.run_publication()
        self.assertEqual(updated, self.host.read_catalog())
        self.assertEqual(3, self.host.uploads)

    def test_resume_uses_existing_draft_and_pinned_default(self):
        self.create_mod("artwork")
        self.commit()
        self.host.fail_upload = True
        with self.assertRaisesRegex(RuntimeError, "interrupted after upload"):
            self.run_publication()
        self.assertEqual("", git(self.repository, "ls-remote", "origin", CATALOG_REF))
        self.assertTrue(self.host.records["artwork-v1.0.0"]["draft"])
        self.run_publication()
        self.assertEqual(1, self.host.uploads)
        self.assertEqual(1, self.default_calls)
        self.assertEqual(1, self.host.read_catalog()["revision"])

    def test_resume_cleanup_without_changing_catalog_or_other_release(self):
        self.create_mod("artwork")
        self.create_mod("other")
        self.commit()
        self.run_publication()

        self.update_payload("artwork", b"changed image")
        self.commit()
        self.host.fail_catalog_check = True
        with self.assertRaisesRegex(RuntimeError, "interrupted after catalog switch"):
            self.run_publication()
        self.assertEqual({"artwork-v1.0.0", "artwork-v1.0.1", "other-v1.0.0"}, set(self.host.records))
        after_switch = self.host.read_catalog()
        self.run_publication()
        self.assertEqual(after_switch, self.host.read_catalog())
        self.assertEqual({"artwork-v1.0.1", "other-v1.0.0"}, set(self.host.records))
        self.assertEqual(3, self.host.uploads)

    def test_changed_draft_bytes_are_not_overwritten(self):
        self.create_mod("artwork")
        self.commit()
        self.host.fail_upload = True
        with self.assertRaisesRegex(RuntimeError, "interrupted after upload"):
            self.run_publication()
        self.host.records["artwork-v1.0.0"]["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match reproducible bytes"):
            self.run_publication()
        self.assertEqual(1, self.host.uploads)
        self.assertEqual("", git(self.repository, "ls-remote", "origin", CATALOG_REF))

    def test_replaces_zero_byte_starter_asset(self):
        self.create_mod("artwork")
        self.commit()
        self.host.fail_upload = True
        with self.assertRaisesRegex(RuntimeError, "interrupted after upload"):
            self.run_publication()
        self.host.records["artwork-v1.0.0"]["assets"][0].update(state="starter", size=0, digest=None)
        self.run_publication()
        self.assertEqual(2, self.host.uploads)
        self.assertEqual(1, self.host.read_catalog()["revision"])


if __name__ == "__main__":
    unittest.main()
