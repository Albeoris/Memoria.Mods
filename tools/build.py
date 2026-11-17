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
DESCRIPTOR_FIELDS = {"id", "name", "package", "description"}
CATALOG_FIELDS = ("id", "groupId", "runtimePackageId", "settingsKey", "name", "description", "category", "format", "delivery", "defaultEnabled", "target")
RELEASE_CONFIG = "releases.json"
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
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


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


def validate_catalog_component(component, component_id):
    require(COMPONENT_ID.fullmatch(component_id) and len(component_id) <= 80, f"Invalid component ID: {component_id}")
    for key in CATALOG_FIELDS:
        if key not in {"defaultEnabled", "target"}:
            require(isinstance(component.get(key), str) and component[key].strip(), f"{component_id}: missing {key}")
    require(component["format"] == "memoria-native" and component["delivery"] == "download",
            f"{component_id}: expected a downloadable native mod")
    require(component.get("defaultEnabled") is True, f"{component_id}: defaultEnabled must be true")
    validate_path(component["runtimePackageId"])
    require(component["groupId"] == "Memoria" and component["runtimePackageId"].startswith("Memoria/")
            and component["runtimePackageId"].count("/") == 1, f"{component_id}: runtime package must be a direct child of Memoria")
    validate_target(component.get("target"))


def entry_mode(path):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT),
            f"Symbolic links and reparse points are forbidden: {path}")
    return info.st_mode


def read_descriptor(folder):
    require(stat.S_ISDIR(entry_mode(folder)), f"Bad component folder: {folder}")
    path = folder / "mod.json"
    require((path.exists() or path.is_symlink()) and stat.S_ISREG(entry_mode(path)), f"Missing regular mod.json in {folder}")
    descriptor = read_json(path)
    require(isinstance(descriptor, dict) and set(descriptor) == DESCRIPTOR_FIELDS,
            f"{path}: descriptor must declare id, name, package and description")
    component_id = descriptor["id"]
    require(isinstance(component_id, str) and len(component_id) <= 80 and COMPONENT_ID.fullmatch(component_id),
            f"{path}: invalid descriptor ID")
    validate_path(component_id)
    require(isinstance(descriptor["name"], str) and descriptor["name"].strip(), f"{path}: name must be text")
    require(descriptor["package"] == "Memoria", f"{path}: official package must be Memoria")
    descriptions = descriptor["description"]
    require(isinstance(descriptions, dict) and "en" in descriptions
            and all(re.fullmatch(r"[a-z]{2}", key) and isinstance(value, str) and value.strip() for key, value in descriptions.items()),
            f"{path}: description must contain nonempty text keyed by two-letter language codes, including en")
    return descriptor


def catalog_metadata(descriptor):
    component_id = descriptor["id"]
    return {
        "id": component_id, "groupId": descriptor["package"], "runtimePackageId": f"Memoria/{component_id}",
        "settingsKey": component_id, "name": descriptor["name"], "description": descriptor["description"]["en"],
        "category": "Items", "format": "memoria-native", "delivery": "download", "defaultEnabled": True,
        "target": {"game": "FFVIII", "editions": ["Classic"], "languages": ["*"]},
    }


def read_release_configuration(root):
    path = root / RELEASE_CONFIG
    if not path.exists() and not path.is_symlink():
        return {}
    require(stat.S_ISREG(entry_mode(path)), f"Invalid release configuration: {path}")
    configuration = read_json(path)
    require(isinstance(configuration, dict), "Release configuration must be keyed by descriptor ID")
    for component_id, release in configuration.items():
        require(COMPONENT_ID.fullmatch(component_id) and isinstance(release, dict)
                and set(release) <= {"version", "engineVersionRange", "releaseNotes"}, f"Invalid release configuration for {component_id}")
        if "version" in release:
            version_key(release["version"])
        if "engineVersionRange" in release:
            engine_range(release["engineVersionRange"])
        require(isinstance(release.get("releaseNotes", ""), str), f"{component_id}: releaseNotes must be text")
    return configuration


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
    known_paths = set()
    files = {}
    notice = None
    pending = [folder]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            relative = path.relative_to(folder)
            name = relative.as_posix()
            validate_path(name)
            require("mod.ini" not in name.casefold().split("/"), f"Player-owned mod.ini is forbidden in the payload: {name}")
            require(name.casefold() not in known_paths, f"Case-colliding payload entry: {name}")
            known_paths.add(name.casefold())
            mode = entry_mode(path)
            namespace = relative.parts[0]
            if namespace.casefold() == "items":
                require(namespace == "items" and (name != "items" or stat.S_ISDIR(mode)), f"Use the exact items/ directory: {name}")
            if name == "NOTICE.md":
                require(stat.S_ISREG(mode), f"Invalid notice in {folder}")
                notice = path
                continue
            if stat.S_ISDIR(mode):
                pending.append(path)
                continue
            require(stat.S_ISREG(mode) and path.suffix.lower() not in UNSUPPORTED_PAYLOAD_TYPES,
                    f"Unsupported payload entry: {name}")
            files[name] = path
    require("mod.json" in files, f"Missing exact-case mod.json in {folder}")
    return files, notice


def resolve_image(document, image):
    require(isinstance(image, str), f"{document}: image must be a resource path")
    parts = [] if image.startswith("/") else document.split("/")[:-1]
    for part in image.removeprefix("/").split("/"):
        if part == ".":
            continue
        if part == "..":
            require(parts, f"{document}: image path escapes the package: {image}")
            parts.pop()
        else:
            validate_path(part)
            parts.append(part)
    return "/".join(parts)


def validate_items(paths):
    item_ids = set()
    for name, path in sorted(paths.items()):
        if not name.startswith("items/") or not name.lower().endswith(".json"):
            continue
        try:
            item = read_json(path)
        except ValueError as error:
            raise ValueError(f"{name}: invalid UTF-8 JSON: {error}") from error
        require(isinstance(item, dict) and "id" in item and {"type", "use"} & item.keys() and item.keys() <= {"id", "type", "use"},
                f"{name}: expected id and at least one of type and use")
        item_id = item["id"]
        require(type(item_id) is int and 0 <= item_id <= 255, f"{name}: id must be an integer from 0 through 255")
        require(item_id not in item_ids, f"{name}: duplicate item ID: {item_id}")
        item_ids.add(item_id)
        if "type" in item:
            require(item["type"] == "Magazine", f"{name}: unsupported item type")
        if "use" in item:
            use = item["use"]
            require(isinstance(use, dict) and set(use) == {"action", "image"} and use["action"] == "ShowImage",
                    f"{name}: expected a ShowImage action with an image path")
            image = resolve_image(name, use["image"])
            require(image.lower().endswith(".png") and image in paths,
                    f"{name}: image must name an exact-case PNG path in this package: {use['image']}")


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


def build(folder, output, descriptor, version, minimum, repository=REPOSITORY, release_notes=""):
    version_key(version)
    engine_range(minimum)
    paths, notice = payload_files(folder)
    validate_items(paths)
    component = catalog_metadata(descriptor)
    inventory = []
    for name, path in sorted(paths.items()):
        size, digest = file_digest(path)
        inventory.append({"path": name, "size": size, "sha256": digest, "mutable": False})
    manifest = {
        "schemaVersion": 1, "sourceId": SOURCE_ID, "componentId": descriptor["id"],
        "runtimePackageId": component["runtimePackageId"], "version": version, "engineVersionRange": minimum,
        "target": component["target"], "files": inventory,
    }
    manifest_data = json_bytes(manifest)
    notice_size = notice.stat().st_size if notice is not None else None
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
    component["latest"] = {
        "version": version, "engineVersionRange": minimum, "releaseTag": tag,
        "releaseNotes": release_notes.strip(),
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
    release = read_release_configuration(Path(__file__).resolve().parent.parent).get(descriptor["id"], {})
    version = args.version or release.get("version", "1.0.0")
    minimum = args.engine_version_range or release.get("engineVersionRange") or latest_engine_range()
    archive, component = build(args.folder, args.output, descriptor, version, minimum, release_notes=release.get("releaseNotes", ""))
    metadata = archive.with_suffix(".catalog-entry.json")
    metadata.write_bytes(json_bytes(component))
    print(f"Built {archive} and {metadata}")


if __name__ == "__main__":
    main()
