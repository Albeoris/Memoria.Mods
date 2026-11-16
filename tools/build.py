"""Build a reproducible native mod ZIP from one authoring folder."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
from urllib.request import urlopen
from zipfile import ZIP_STORED, ZipFile, ZipInfo


SOURCE_ID = "memoria-official"
REPOSITORY = "Albeoris/Memoria.Mods"
ENGINE_RELEASE_URL = "https://github.com/Albeoris/Memoria.FF8/releases/latest"
VERSION = re.compile(r"(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})")
NUGET_MINIMUM = re.compile(r"(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})(?:\.([1-9][0-9]{0,9}))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
COMPONENT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_ASSET_SIZE = 2 * 1024**3
CHUNK_SIZE = 1024**2
DESCRIPTOR_FIELDS = ("id", "groupId", "runtimePackageId", "settingsKey", "name", "description", "category", "format", "delivery", "defaultEnabled", "target")
UNSUPPORTED_PAYLOAD_TYPES = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".msi", ".lnk"}
RESERVED_PATH = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.IGNORECASE)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version_key(version):
    match = VERSION.fullmatch(version) if isinstance(version, str) else None
    require(match is not None, f"Expected a stable major.minor.patch version, got {version!r}")
    result = tuple(map(int, match.groups()))
    require(all(part <= 2147483647 for part in result), f"Version is out of range: {version}")
    return result


def engine_range(value):
    require(isinstance(value, str) and value.startswith("[") and value.endswith(",)"),
            "Engine requirements use [minimum,) with an inclusive minimum")
    match = NUGET_MINIMUM.fullmatch(value[1:-2])
    require(match and all(int(part) <= 2147483647 for part in match.groups()[:4] if part is not None),
            f"Invalid normalized NuGet minimum: {value}")
    identifiers = (match[5] or "").split(".")
    require(all(not item.isdigit() or len(item) == 1 or not item.startswith("0") for item in identifiers),
            f"Invalid prerelease minimum: {value}")
    return value


def latest_engine_range():
    with urlopen(ENGINE_RELEASE_URL, timeout=30) as response:
        prefix = ENGINE_RELEASE_URL.removesuffix("latest") + "tag/v"
        url = response.geturl()
        require(url.startswith(prefix), f"Unexpected stable engine release URL: {url}")
        version = url.removeprefix(prefix)
    version_key(version)
    return f"[{version},)"


def json_bytes(data):
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Repeated JSON property: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique_object)


def validate_path(path):
    require(isinstance(path, str), f"Expected a resource path, got {path!r}")
    for part in path.split("/"):
        require(part and part not in {".", ".."} and not part.endswith((" ", "."))
                and not RESERVED_PATH.match(part) and not re.search(r'[<>:"\\|?*\x00-\x1f]', part),
                f"Non-portable resource path: {path!r}")


def validate_target(target):
    require(isinstance(target, dict) and set(target) == {"game", "editions", "languages"},
            "Target must declare game, editions and languages")
    require(target["game"] == "FFVIII", "Supported game is FFVIII")
    editions = target["editions"]
    require(isinstance(editions, list) and editions and all(value in ("Classic", "Remastered") for value in editions)
            and len(editions) == len(set(editions)), "Target editions must be Classic or Remastered")
    languages = target["languages"]
    require(isinstance(languages, list) and languages
            and (languages == ["*"] or all(isinstance(value, str) and re.fullmatch(r"[a-z]{2}", value) for value in languages))
            and len(languages) == len(set(languages)), "Target must use [\"*\"] or distinct two-letter language codes")


def validate_descriptor(descriptor, component_id):
    require(isinstance(descriptor, dict), "Descriptor must be an object")
    require(COMPONENT_ID.fullmatch(component_id) and descriptor.get("id") == component_id,
            f"Invalid descriptor ID for component {component_id}")
    for key in DESCRIPTOR_FIELDS:
        if key not in {"defaultEnabled", "target"}:
            require(isinstance(descriptor.get(key), str) and descriptor[key].strip(), f"{component_id}: missing {key}")
    require(descriptor["format"] == "memoria-native" and descriptor["delivery"] == "download",
            f"{component_id}: expected a downloadable native mod")
    require(descriptor.get("defaultEnabled") is True, f"{component_id}: defaultEnabled must be true")
    validate_path(descriptor["runtimePackageId"])
    require(descriptor["groupId"] == "Memoria" and descriptor["runtimePackageId"].startswith("Memoria/")
            and descriptor["runtimePackageId"].count("/") == 1, f"{component_id}: runtime package must be a direct child of Memoria")
    validate_target(descriptor.get("target"))


def read_descriptor(folder):
    require(folder.is_dir() and not folder.is_symlink(), f"Bad component folder: {folder}")
    path = folder / "mod.json"
    require(path.is_file() and not path.is_symlink(), f"Missing regular mod.json in {folder}")
    descriptor = read_json(path)
    validate_descriptor(descriptor, folder.name)
    require(set(descriptor) <= set(DESCRIPTOR_FIELDS) | {"version", "engineVersionRange", "releaseNotes"},
            f"{folder.name}: unexpected authoring fields")
    if "version" in descriptor:
        version_key(descriptor["version"])
    if "engineVersionRange" in descriptor:
        engine_range(descriptor["engineVersionRange"])
    require(isinstance(descriptor.get("releaseNotes", ""), str), f"{folder.name}: releaseNotes must be text")
    return descriptor


def digest_stream(stream, limit=None):
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
        size += len(chunk)
        require(limit is None or size <= limit, "File exceeds its declared size")
        digest.update(chunk)
    return size, digest.hexdigest()


def file_digest(path):
    with path.open("rb") as stream:
        return digest_stream(stream)


def payload_files(folder):
    payload = folder / "payload"
    require(payload.is_dir() and not payload.is_symlink(), f"Missing regular payload/ in {folder}")
    known_paths = set()
    files = {}
    for path in sorted(payload.rglob("*")):
        name = path.relative_to(payload).as_posix()
        validate_path(name)
        require("mod.ini" not in name.casefold().split("/"), f"Player-owned mod.ini is forbidden in the payload: {name}")
        require(name.casefold() not in known_paths, f"Case-colliding payload entry: {name}")
        known_paths.add(name.casefold())
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        require(stat.S_ISREG(mode) and path.suffix.lower() not in UNSUPPORTED_PAYLOAD_TYPES,
                f"Unsupported payload entry: {name}")
        files[name] = path
    require(files, f"Empty payload in {folder}")
    return files


def archive_entry(name, size):
    entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    entry.file_size = size
    return entry


def validate_archive(path, manifest, notice_size):
    inventories = {f"payload/{item['path']}": item for item in manifest["files"]}
    names = ["mod.manifest.json", *inventories]
    if notice_size is not None:
        names.append("NOTICE.md")
    with ZipFile(path) as archive:
        require(archive.namelist() == names, f"Invalid ZIP inventory: {path}")
        require(json.loads(archive.read("mod.manifest.json")) == manifest, f"Invalid manifest: {path}")
        for name, item in inventories.items():
            with archive.open(name) as stream:
                actual = digest_stream(stream, item["size"])
            require(actual == (item["size"], item["sha256"]), f"Invalid archived payload: {name}")
        if notice_size is not None:
            with archive.open("NOTICE.md") as stream:
                require(digest_stream(stream, notice_size)[0] == notice_size, "Invalid notice size")
        return sum(item.file_size for item in archive.infolist())


def build(folder, output, descriptor, version, minimum, repository=REPOSITORY):
    version_key(version)
    engine_range(minimum)
    paths = payload_files(folder)
    inventory = []
    for name, path in sorted(paths.items()):
        size, digest = file_digest(path)
        inventory.append({"path": name, "size": size, "sha256": digest, "mutable": False})
    manifest = {
        "schemaVersion": 1, "sourceId": SOURCE_ID, "componentId": descriptor["id"],
        "runtimePackageId": descriptor["runtimePackageId"], "version": version, "engineVersionRange": minimum,
        "target": descriptor["target"], "files": inventory,
    }
    manifest_data = json_bytes(manifest)
    notice = folder / "NOTICE.md"
    require(not notice.is_symlink() and (not notice.exists() or notice.is_file()), f"Invalid notice in {folder}")
    notice_size = notice.stat().st_size if notice.exists() else None
    total_size = len(manifest_data) + sum(item["size"] for item in inventory) + (notice_size or 0)
    require(0 < total_size < MAX_ASSET_SIZE, f"{folder.name}: archive must fit the GitHub Release per-asset limit")
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"{descriptor['id']}-{version}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_STORED) as archive:
        archive.writestr(archive_entry("mod.manifest.json", len(manifest_data)), manifest_data)
        for item in inventory:
            with paths[item["path"]].open("rb") as source, archive.open(archive_entry(f"payload/{item['path']}", item["size"]), "w") as destination:
                shutil.copyfileobj(source, destination, CHUNK_SIZE)
        if notice_size is not None:
            with notice.open("rb") as source, archive.open(archive_entry("NOTICE.md", notice_size), "w") as destination:
                shutil.copyfileobj(source, destination, CHUNK_SIZE)
    unpacked_size = validate_archive(archive_path, manifest, notice_size)
    size, digest = file_digest(archive_path)
    require(0 < size < MAX_ASSET_SIZE, f"{archive_path.name}: archive must be smaller than 2 GiB")
    tag = f"{descriptor['id']}-v{version}"
    component = {key: descriptor[key] for key in DESCRIPTOR_FIELDS}
    component["latest"] = {
        "version": version, "engineVersionRange": minimum, "releaseTag": tag,
        "releaseNotes": descriptor.get("releaseNotes", "").strip(),
        "artifact": {
            "url": f"https://github.com/{repository}/releases/download/{tag}/{archive_path.name}",
            "size": size, "unpackedSize": unpacked_size, "sha256": digest,
        },
    }
    return archive_path, component


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    parser.add_argument("--version", help="Build a specific version selected by publication")
    parser.add_argument("--engine-version-range", help="Build with a specific resolved engine requirement")
    args = parser.parse_args()
    descriptor = read_descriptor(args.folder)
    version = args.version or descriptor.get("version", "1.0.0")
    minimum = args.engine_version_range or descriptor.get("engineVersionRange") or latest_engine_range()
    archive, component = build(args.folder, args.output, descriptor, version, minimum)
    metadata = archive.with_suffix(".catalog-entry.json")
    metadata.write_bytes(json_bytes(component))
    print(f"Built {archive} and {metadata}")


if __name__ == "__main__":
    main()
