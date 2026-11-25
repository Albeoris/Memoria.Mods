"""Build a reproducible native mod ZIP from one authoring folder."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
from struct import unpack
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo
from zlib import crc32


SOURCE_ID = "memoria-official"
REPOSITORY = "Albeoris/Memoria.Mods"
ENGINE_RELEASE_URL = "https://github.com/Albeoris/Memoria.FF8/releases/latest"
VERSION = re.compile(r"(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})")
NUGET_MINIMUM = re.compile(r"(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})(?:\.([1-9][0-9]{0,9}))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
COMPONENT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_ASSET_SIZE = 2 * 1024**3
CHUNK_SIZE = 1024**2
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
MAX_PNG_SIZE = 64 * 1024**2
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
DESCRIPTOR_FIELDS = {"id", "name", "package", "description"}
LANGUAGE_FIELDS = {"kind", "languageOffers"}
DEPENDENCY_FIELDS = {"kind", "nativeVariants"}
OPTIONAL_TYPED_FIELDS = {"dependencies"}
LOCALE = re.compile(r"[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?(?:-[a-z0-9]{5,8}|-[0-9][a-z0-9]{3})*")
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
    expected_activation = "languageOffers" not in component and "nativeVariants" not in component
    require(component.get("defaultEnabled") is expected_activation, f"{component_id}: defaultEnabled must be {str(expected_activation).lower()}")
    validate_path(component["runtimePackageId"])
    require(component["runtimePackageId"].startswith("Memoria/") and component["runtimePackageId"].count("/") == 1,
            f"{component_id}: runtime package must be a direct child of Memoria")
    validate_target(component.get("target"))


def language_target(offers):
    return variant_target(pair for offer in offers for pair in offer["nativeVariants"])


def variant_target(pairs):
    pairs = list(pairs)
    return {
        "game": "FFVIII",
        "editions": sorted({pair["edition"] for pair in pairs}),
        "languages": sorted({pair["language"] for pair in pairs}),
    }


def validate_native_variants(pairs, context):
    require(isinstance(pairs, list) and pairs, f"{context}: nativeVariants must be nonempty")
    seen = set()
    for pair in pairs:
        require(isinstance(pair, dict) and set(pair) == {"edition", "language"}, f"{context}: invalid native variant")
        edition, language = pair["edition"], pair["language"]
        require(edition in ("Classic", "Remastered") and isinstance(language, str) and re.fullmatch(r"[a-z]{2}", language) and language != "jp",
                f"{context}: invalid native edition and base language")
        require((edition, language) not in seen, f"{context}: repeated native variant")
        seen.add((edition, language))


def validate_language_offers(offers):
    require(isinstance(offers, list) and offers, "Language packages require at least one language offer")
    options = set()
    for offer in offers:
        require(isinstance(offer, dict) and set(offer) == {"locale", "label", "option", "nativeVariants"},
                "Language offers must declare locale, label, option and nativeVariants")
        locale = offer["locale"]
        require(isinstance(locale, str) and LOCALE.fullmatch(locale) and locale.split("-")[0] != "jp",
                f"Invalid display-language tag: {locale!r}")
        for field in ("label", "option"):
            value = offer[field]
            require(isinstance(value, str) and value.strip() == value and value and not re.search(r"[\x00-\x1f\x7f-\x9f]", value),
                    f"{locale}: {field} must be nonblank text without control characters")
        option = offer["option"]
        require(option.casefold() not in options, f"Repeated language option: {option}")
        options.add(option.casefold())
        validate_native_variants(offer["nativeVariants"], locale)


def validate_dependency_ids(ids, owner):
    require(isinstance(ids, list) and len(ids) <= 32 and all(isinstance(value, str) and len(value) <= 80 and COMPONENT_ID.fullmatch(value) and value != owner for value in ids)
            and len(ids) == len(set(ids)), f"{owner}: invalid or duplicate dependency IDs")


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
    require(isinstance(descriptor, dict) and DESCRIPTOR_FIELDS <= descriptor.keys(),
            f"{path}: descriptor must declare id, name, package and description")
    kind = descriptor.get("kind")
    allowed_fields = {"language": LANGUAGE_FIELDS, "dependency": DEPENDENCY_FIELDS}
    if "kind" in descriptor:
        required = allowed_fields.get(kind) if isinstance(kind, str) else None
        require(required is not None and required <= descriptor.keys() and descriptor.keys() <= DESCRIPTOR_FIELDS | required | OPTIONAL_TYPED_FIELDS
                and descriptor["package"] == "Localization", f"{path}: invalid typed Localization descriptor")
        if kind == "language":
            validate_language_offers(descriptor["languageOffers"])
        else:
            validate_native_variants(descriptor["nativeVariants"], descriptor["id"])
    else:
        require(descriptor.keys() == DESCRIPTOR_FIELDS, f"{path}: ordinary descriptor must declare exactly id, name, package and description")
    component_id = descriptor["id"]
    require(isinstance(component_id, str) and len(component_id) <= 80 and COMPONENT_ID.fullmatch(component_id),
            f"{path}: invalid descriptor ID")
    validate_dependency_ids(descriptor.get("dependencies", []), component_id)
    validate_path(component_id)
    require(isinstance(descriptor["name"], str) and descriptor["name"].strip(), f"{path}: name must be text")
    package = descriptor["package"]
    require(isinstance(package, str) and package.strip() and not re.search(r"[\x00-\x1f\x7f-\x9f]", package),
            f"{path}: package must be nonblank text without control characters")
    descriptions = descriptor["description"]
    require(isinstance(descriptions, dict) and "en" in descriptions
            and all(re.fullmatch(r"[a-z]{2}", key) and isinstance(value, str) and value.strip() for key, value in descriptions.items()),
            f"{path}: description must contain nonempty text keyed by two-letter language codes, including en")
    return descriptor


def catalog_metadata(descriptor):
    component_id = descriptor["id"]
    component = {
        "id": component_id, "groupId": descriptor["package"], "runtimePackageId": f"Memoria/{component_id}",
        "settingsKey": component_id, "name": descriptor["name"], "description": descriptor["description"]["en"],
        "category": descriptor["package"], "format": "memoria-native", "delivery": "download", "defaultEnabled": True,
        "target": {"game": "FFVIII", "editions": ["Classic"], "languages": ["*"]},
    }
    if descriptor.get("kind") == "language":
        offers = descriptor["languageOffers"]
        component.update(groupId="Memoria", defaultEnabled=False, target=language_target(offers), languageOffers=offers)
    elif descriptor.get("kind") == "dependency":
        variants = descriptor["nativeVariants"]
        component.update(groupId="Memoria", defaultEnabled=False, target=variant_target(variants), nativeVariants=variants)
    return component


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
            with path.open("rb") as stream:
                require(stream.read(len(LFS_POINTER)) != LFS_POINTER, f"Unhydrated Git LFS pointer: {name}")
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


def validate_png(path, name):
    prefix = f"{name}: invalid PNG"
    file_size = path.stat().st_size
    require(file_size <= MAX_PNG_SIZE, f"{prefix}: file exceeds 64 MiB")
    with path.open("rb") as stream:
        require(stream.read(8) == PNG_SIGNATURE, f"{prefix}: incorrect signature")
        color = None
        palette = False
        image_data_size = 0
        reading_image_data = False
        image_data_ended = False
        while True:
            header = stream.read(8)
            require(len(header) == 8, f"{prefix}: missing IEND or incomplete chunk header")
            size, kind = unpack(">I4s", header)
            require(size <= file_size - stream.tell() - 4 and kind.isalpha() and kind[2:3].isupper(),
                    f"{prefix}: invalid chunk length or type")
            data = stream.read(size)
            checksum = stream.read(4)
            require(len(data) == size and len(checksum) == 4 and crc32(data, crc32(kind)) == int.from_bytes(checksum, "big"),
                    f"{prefix}: invalid {kind.decode('ascii')} CRC")
            require(color is not None or kind == b"IHDR", f"{prefix}: missing initial IHDR")
            if kind == b"IHDR":
                require(color is None and size == 13, f"{prefix}: invalid IHDR")
                width, height, depth, color, compression, filtering, interlace = unpack(">IIBBBBB", data)
                require(0 < width <= 8192 and 0 < height <= 8192 and width * height <= 32_000_000,
                        f"{prefix}: unsupported dimensions")
                require(depth in PNG_DEPTHS.get(color, ()) and compression == filtering == 0 and interlace in (0, 1),
                        f"{prefix}: unsupported IHDR fields")
            elif kind == b"PLTE":
                require(not palette and not reading_image_data and not image_data_ended and color not in (0, 4)
                        and 3 <= size <= 768 and size % 3 == 0 and (color != 3 or size // 3 <= 2**depth),
                        f"{prefix}: invalid PLTE")
                palette = True
            elif kind == b"IDAT":
                require(not image_data_ended and (color != 3 or palette), f"{prefix}: misplaced IDAT or missing PLTE")
                image_data_size += size
                reading_image_data = True
            elif kind == b"IEND":
                require(image_data_size > 0, f"{prefix}: missing image data")
                require(size == 0 and not stream.read(1), f"{prefix}: invalid IEND")
                return
            else:
                require(kind[:1].islower(), f"{prefix}: unknown critical chunk {kind.decode('ascii')}")
                if reading_image_data:
                    image_data_ended = True
                    reading_image_data = False


def validate_items(paths):
    item_ids = set()
    validated_images = set()
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
        require(type(item_id) is int and 1 <= item_id <= 255, f"{name}: id must be an integer from 1 through 255")
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
            if image not in validated_images:
                validate_png(paths[image], image)
                validated_images.add(image)


def archive_entry(name, size, compression):
    entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    entry.file_size = size
    entry.compress_type = compression
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
    compression = ZIP_DEFLATED if descriptor.get("kind") in ("language", "dependency") else ZIP_STORED
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(archive_entry("mod.manifest.json", len(manifest_data), compression), manifest_data)
        for item in inventory:
            with paths[item["path"]].open("rb") as source, archive.open(archive_entry(f"payload/{item['path']}", item["size"], compression), "w") as destination:
                shutil.copyfileobj(source, destination, CHUNK_SIZE)
        if notice_size is not None:
            with notice.open("rb") as source, archive.open(archive_entry("NOTICE.md", notice_size, compression), "w") as destination:
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
    configuration = read_release_configuration(Path(__file__).resolve().parent.parent)
    release = configuration.get(descriptor["id"], {})
    version = args.version or release.get("version", "1.0.0")
    minimum = args.engine_version_range or release.get("engineVersionRange") or latest_engine_range()
    archive, component = build(args.folder, args.output, descriptor, version, minimum, release_notes=release.get("releaseNotes", ""))
    if descriptor.get("dependencies"):
        component["dependencies"] = [{"id": dependency, "version": configuration.get(dependency, {}).get("version", "1.0.0")} for dependency in descriptor["dependencies"]]
    metadata = archive.with_suffix(".catalog-entry.json")
    metadata.write_bytes(json_bytes(component))
    print(f"Built {archive} and {metadata}")


if __name__ == "__main__":
    main()
