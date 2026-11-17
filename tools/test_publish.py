import copy
import json
from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from zlib import compress, crc32

from build import PNG_SIGNATURE, build, catalog_metadata, engine_range, file_digest, json_bytes, read_descriptor, read_release_configuration
from publish import CATALOG_FILE, CATALOG_REF, RELEASE_MARKER, RELEASE_SUFFIX, GIT_AUTHOR, git, publish


def png_chunk(kind, data):
    return pack(">I", len(data)) + kind + data + pack(">I", crc32(kind + data))


def png_header(width=1, height=1):
    return png_chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))


PNG_IMAGE_DATA = png_chunk(b"IDAT", compress(b"\x00\x00\x00\x00\xff"))
PNG_END = png_chunk(b"IEND", b"")
TEST_PNG = PNG_SIGNATURE + png_header() + PNG_IMAGE_DATA + PNG_END


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

    def create_mod(self, component_id, *, minimum=None, folder_name=None):
        folder = self.repository / "mods" / (folder_name or component_id)
        folder.mkdir(parents=True)
        (folder / "image.png").write_bytes(TEST_PNG)
        descriptor = {
            "id": component_id, "name": f"Title of {component_id}", "package": "Memoria",
            "description": {"en": f"Description of {component_id}.", "ru": "Описание."},
        }
        if minimum:
            self.configure_release(component_id, engineVersionRange=minimum)
        (folder / "mod.json").write_bytes(json_bytes(descriptor))
        return folder

    def configure_release(self, component_id, **release):
        configuration = read_release_configuration(self.repository)
        configuration[component_id] = release
        (self.repository / "releases.json").write_bytes(json_bytes(configuration))

    def update_payload(self, component_id, data):
        (self.repository / "mods" / component_id / "image.png").write_bytes(data)

    def commit(self):
        git(self.repository, "add", ".")
        git(self.repository, *GIT_AUTHOR, "commit", "-m", "Update source")

    def resolve_minimum(self):
        self.default_calls += 1
        return "[1.2.3,)"

    def run_publication(self):
        publish(self.repository, self.host, self.resolve_minimum)

    def test_package_reproducibility_and_inventory(self):
        folder = self.create_mod("artwork", folder_name="Package Folder")
        descriptor = read_descriptor(folder)
        (folder / "NOTICE.md").write_bytes(b"Attribution")
        first, component = build(folder, self.root / "first", descriptor, "1.0.0", "[1.0.0-beta.1,)")
        second, repeated = build(folder, self.root / "second", descriptor, "1.0.0", "[1.0.0-beta.1,)")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(component, repeated)
        with ZipFile(first) as archive:
            manifest = json.loads(archive.read("mod.manifest.json"))
            self.assertEqual(["image.png", "mod.json"], [item["path"] for item in manifest["files"]])
            self.assertEqual([False, False], [item["mutable"] for item in manifest["files"]])
            self.assertEqual("artwork", manifest["componentId"])
            self.assertEqual("Memoria/artwork", manifest["runtimePackageId"])
            self.assertEqual(descriptor, json.loads(archive.read("payload/mod.json")))
            self.assertEqual(TEST_PNG, archive.read("payload/image.png"))
            self.assertEqual(b"Attribution", archive.read("NOTICE.md"))
            self.assertEqual(sum(entry.file_size for entry in archive.infolist()), component["latest"]["artifact"]["unpackedSize"])
        self.assertEqual("artwork", component["settingsKey"])
        self.assertEqual("Memoria", component["groupId"])
        self.assertEqual("Memoria", component["category"])
        self.assertEqual("Title of artwork", component["name"])
        self.assertEqual(descriptor["description"]["en"], component["description"])
        self.assertTrue(component["defaultEnabled"])

    def test_reject_player_owned_configuration(self):
        folder = self.create_mod("artwork")
        descriptor = read_descriptor(folder)
        for name in ("mod.ini", "nested/MoD.InI", "MOD.INI/image.png"):
            with self.subTest(name=name):
                configuration = folder / name
                configuration.parent.mkdir(parents=True, exist_ok=True)
                configuration.write_bytes(b"name = Artwork\r\n")
                with self.assertRaisesRegex(ValueError, "Player-owned mod.ini is forbidden"):
                    build(folder, self.root / "archive", descriptor, "1.0.0", "[1.0.0,)")
                configuration.unlink()

    def test_authoring_descriptor_rejects_generated_fields(self):
        folder = self.create_mod("artwork")
        descriptor = read_descriptor(folder)
        descriptor["defaultEnabled"] = False
        (folder / "mod.json").write_bytes(json_bytes(descriptor))
        with self.assertRaisesRegex(ValueError, "descriptor must declare"):
            read_descriptor(folder)

    def test_package_item_overrides_and_image_paths(self):
        folder = self.create_mod("artwork")
        (folder / "items" / "nested").mkdir(parents=True)
        (folder / "items" / "nested" / "Cover.PNG").write_bytes(TEST_PNG)
        documents = {
            "nested/arbitrary.json": {"id": 1, "type": "Magazine", "use": {"action": "ShowImage", "image": "./Cover.PNG"}},
            "nested/255.json": {"id": 163, "use": {"action": "ShowImage", "image": "../../image.png"}},
            "nested/root.JSON": {"id": 164, "use": {"action": "ShowImage", "image": "/image.png"}},
            "nested/plain.json": {"id": 165, "use": {"action": "ShowImage", "image": "Cover.PNG"}},
            "nested/.json": {"id": 166, "type": "Magazine"},
            "nested/type.json": {"id": 255, "type": "Magazine"},
        }
        for name, document in documents.items():
            (folder / "items" / name).write_bytes(json_bytes(document))
        archive_path, _ = build(folder, self.root / "archive", read_descriptor(folder), "1.0.0", "[1.0.0,)")
        with ZipFile(archive_path) as archive:
            for name, document in documents.items():
                self.assertEqual(document, json.loads(archive.read(f"payload/items/{name}")))
            self.assertEqual(TEST_PNG, archive.read("payload/items/nested/Cover.PNG"))

    def test_reject_invalid_item_overrides(self):
        folder = self.create_mod("artwork")
        descriptor = read_descriptor(folder)
        path = folder / "items" / "anything.json"
        path.parent.mkdir()
        image_action = {"action": "ShowImage", "image": "/image.png"}
        cases = (
            ("expected id", {"schemaVersion": 1, "items": []}),
            ("expected id", {"id": 163}),
            ("expected id", {"id": 163, "type": "Magazine", "extra": True}),
            ("integer from 1 through 255", {"id": True, "type": "Magazine"}),
            ("integer from 1 through 255", {"id": 0, "type": "Magazine"}),
            ("integer from 1 through 255", {"id": 256, "type": "Magazine"}),
            ("unsupported item type", {"id": 163, "type": "magazine"}),
            ("expected a ShowImage action", {"id": 163, "use": None}),
            ("expected a ShowImage action", {"id": 163, "use": {**image_action, "action": "showImage"}}),
            ("expected a ShowImage action", {"id": 163, "use": {**image_action, "extra": True}}),
            ("image must be a resource path", {"id": 163, "use": {**image_action, "image": 3}}),
        )
        bad_paths = {
            "escapes the package": ("../../image.png", "/../image.png"),
            "Non-portable resource path": ("//server/image.png", "C:/image.png", "\\\\server\\image.png",
                                           "./sub\\image.png", "file:///image.png", "/NUL.png", "/space /image.png"),
            "exact-case PNG path": ("/Image.png", "/missing.png", "/mod.json"),
        }
        cases += tuple((message, {"id": 163, "use": {**image_action, "image": image}})
                       for message, images in bad_paths.items() for image in images)
        for expected_error, item in cases:
            with self.subTest(item=item):
                path.write_bytes(json_bytes(item))
                with self.assertRaisesRegex(ValueError, expected_error):
                    build(folder, self.root / "archive", descriptor, "1.0.0", "[1.0.0,)")
        for raw in (b'{"id": 163, "id": 164, "type": "Magazine"}', b'{"id": 163,"type": "\xff"}'):
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "invalid UTF-8 JSON"):
                build(folder, self.root / "archive", descriptor, "1.0.0", "[1.0.0,)")
        item = {"id": 163, "type": "Magazine"}
        path.write_bytes(json_bytes(item))
        (path.parent / "duplicate.json").write_bytes(json_bytes(item))
        with self.assertRaisesRegex(ValueError, "duplicate item ID: 163"):
            build(folder, self.root / "archive", descriptor, "1.0.0", "[1.0.0,)")

    def test_reject_invalid_png_used_by_item(self):
        folder = self.create_mod("artwork")
        descriptor = read_descriptor(folder)
        document = folder / "items" / "item.json"
        document.parent.mkdir()
        document.write_bytes(json_bytes({"id": 163, "use": {"action": "ShowImage", "image": "/image.png"}}))
        image = folder / "image.png"
        cases = {
            "incorrect signature": b"original cover",
            "unsupported dimensions": PNG_SIGNATURE + png_header(8193) + PNG_IMAGE_DATA + PNG_END,
            "invalid IHDR CRC": TEST_PNG[:29] + bytes([TEST_PNG[29] ^ 1]) + TEST_PNG[30:],
            "missing initial IHDR": PNG_SIGNATURE + PNG_IMAGE_DATA + PNG_END,
            "missing image data": PNG_SIGNATURE + png_header() + PNG_END,
            "missing IEND": TEST_PNG[:-12],
            "invalid IEND": TEST_PNG + b"trailing bytes",
            "invalid chunk length": TEST_PNG[:33] + pack(">I", 0xFFFFFFFF) + TEST_PNG[37:],
        }
        for error, data in cases.items():
            with self.subTest(error=error):
                image.write_bytes(data)
                with self.assertRaisesRegex(ValueError, f"invalid PNG: {error}"):
                    build(folder, self.root / "archive", descriptor, "1.0.0", "[1.0.0,)")

    def test_reject_case_colliding_entries(self):
        folder = self.create_mod("artwork")
        if (folder / "Image.png").exists():
            self.skipTest("Requires a case-sensitive filesystem")
        (folder / "Image.png").write_bytes(b"case collision")
        with self.assertRaisesRegex(ValueError, "Case-colliding payload entry"):
            build(folder, self.root / "archive", read_descriptor(folder), "1.0.0", "[1.0.0,)")

    def test_reject_linked_payload_directory(self):
        folder = self.create_mod("artwork")
        external = self.root / "external"
        external.mkdir()
        try:
            (folder / "items").symlink_to(external, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Cannot create a symbolic link: {error}")
        with self.assertRaisesRegex(ValueError, "Symbolic links and reparse points are forbidden"):
            build(folder, self.root / "archive", read_descriptor(folder), "1.0.0", "[1.0.0,)")

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

    def test_only_changed_release_configuration_publishes_a_version(self):
        self.create_mod("artwork", minimum="[1.0.0,)", folder_name="Artwork Files")
        self.create_mod("other")
        self.commit()
        self.run_publication()
        initial = self.host.read_catalog()
        self.configure_release("artwork", engineVersionRange="[1.0.1,)", releaseNotes="New engine contract.")
        self.commit()
        self.run_publication()
        updated = self.host.read_catalog()
        self.assertEqual(initial["revision"] + 1, updated["revision"])
        self.assertEqual(initial["components"][1], updated["components"][1])
        latest = updated["components"][0]["latest"]
        self.assertEqual("1.0.1", latest["version"])
        self.assertEqual("[1.0.1,)", latest["engineVersionRange"])
        self.assertEqual("New engine contract.", latest["releaseNotes"])

    def test_package_group_can_change_without_resetting_identity(self):
        folder = self.create_mod("artwork")
        descriptor = read_descriptor(folder)
        descriptor["package"] = "Original group"
        (folder / "mod.json").write_bytes(json_bytes(descriptor))
        self.commit()
        self.run_publication()
        initial = self.host.read_catalog()["components"][0]

        descriptor["package"] = "Different group"
        (folder / "mod.json").write_bytes(json_bytes(descriptor))
        self.commit()
        self.run_publication()
        updated_catalog = self.host.read_catalog()
        updated = updated_catalog["components"][0]
        for key in ("id", "runtimePackageId", "settingsKey", "defaultEnabled"):
            self.assertEqual(initial[key], updated[key])
        self.assertEqual("Different group", updated["groupId"])
        self.assertEqual("Different group", updated["category"])
        self.assertEqual("1.0.1", updated["latest"]["version"])
        self.run_publication()
        self.assertEqual(updated_catalog, self.host.read_catalog())

    def test_girl_next_door_identity_transition_requires_new_engine(self):
        component_id = "girl-next-door"
        self.create_mod(component_id, minimum="[0.8.10,)")
        self.commit()

        def legacy_metadata(descriptor):
            return {**catalog_metadata(descriptor), "runtimePackageId": "Memoria/Girl Next Door", "settingsKey": "Girl Next Door", "category": "Items"}

        with patch("publish.catalog_metadata", legacy_metadata), patch("build.catalog_metadata", legacy_metadata):
            self.run_publication()
        legacy = self.host.read_catalog()
        self.assertEqual("Memoria/Girl Next Door", legacy["components"][0]["runtimePackageId"])

        self.update_payload(component_id, b"new resource")
        self.commit()
        with self.assertRaisesRegex(ValueError, "cannot change stable runtime package"):
            self.run_publication()
        self.assertEqual(legacy, self.host.read_catalog())
        self.assertEqual({"girl-next-door-v1.0.0"}, set(self.host.records))

        self.configure_release(component_id, engineVersionRange="[0.8.11,)")
        self.commit()
        self.run_publication()
        updated = self.host.read_catalog()["components"][0]
        self.assertEqual("Memoria/girl-next-door", updated["runtimePackageId"])
        self.assertEqual("girl-next-door", updated["settingsKey"])
        self.assertEqual("[0.8.11,)", updated["latest"]["engineVersionRange"])
        self.assertEqual({"girl-next-door-v1.0.1"}, set(self.host.records))

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

    def test_namespaced_identity_replaces_catalog_atomically_and_resumes_retirement(self):
        previous_id = "girl-next-door"
        component_id = "memoria-girl-next-door"
        folder = self.create_mod(previous_id, minimum="[0.8.11,)")
        self.configure_release(previous_id, version="1.0.3", engineVersionRange="[0.8.11,)")
        self.commit()
        self.run_publication()
        before = self.host.read_catalog()

        descriptor = read_descriptor(folder)
        descriptor["id"] = component_id
        (folder / "mod.json").write_bytes(json_bytes(descriptor))
        (self.repository / "releases.json").write_bytes(json_bytes({component_id: {"engineVersionRange": "[0.8.11,)"}}))
        self.commit()
        with self.assertRaisesRegex(ValueError, "requires Memoria 0.8.12"):
            self.run_publication()
        self.assertEqual(before, self.host.read_catalog())

        self.configure_release(component_id, engineVersionRange="[0.8.12,)")
        self.commit()
        self.host.fail_catalog_check = True
        with self.assertRaisesRegex(RuntimeError, "interrupted after catalog switch"):
            self.run_publication()
        switched = self.host.read_catalog()
        self.assertEqual([component_id], [entry["id"] for entry in switched["components"]])
        self.assertEqual("1.0.4", switched["components"][0]["latest"]["version"])
        self.assertEqual({"girl-next-door-v1.0.3", "memoria-girl-next-door-v1.0.4"}, set(self.host.records))
        self.run_publication()
        self.assertEqual(switched, self.host.read_catalog())
        self.assertEqual({"memoria-girl-next-door-v1.0.4"}, set(self.host.records))

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
