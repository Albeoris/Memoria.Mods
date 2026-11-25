"""Reconcile the public catalog and releases with a committed main checkout."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from build import (RELEASE_CONFIG, REPOSITORY, SOURCE_ID, build, catalog_metadata, digest_stream, engine_range, entry_mode, json_bytes,
                   language_target, latest_engine_range, read_descriptor, read_json, read_release_configuration, require, unique_object,
                   validate_catalog_component, validate_dependency_ids, validate_language_offers, validate_native_variants, variant_target, version_key)


CATALOG_BRANCH = "catalog"
CATALOG_FILE = "catalog-v1.json"
LANGUAGE_FILE = "languages-v1.json"
CATALOG_FILES = (CATALOG_FILE, LANGUAGE_FILE)
RELEASE_MARKER = "<!-- memoria-official-v1:"
RELEASE_SUFFIX = " -->"
DIGEST = re.compile(r"[a-f0-9]{64}")
GIT_ID = re.compile(r"[a-f0-9]{40}")
CATALOG_REF = f"refs/heads/{CATALOG_BRANCH}"
RETIRED_COMPONENT_IDS = {"memoria-girl-next-door": "girl-next-door"}
GIT_AUTHOR = ("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com")


def retry(action, description, delays=(0, 1, 2, 4, 8, 16)):
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            return action()
        except (OSError, ValueError) as error:
            last_error = error
    raise RuntimeError(f"{description}: {last_error}") from last_error


def git(root, *args, stdin=None):
    return subprocess.run(["git", *args], cwd=root, input=stdin, text=True, stdout=subprocess.PIPE, check=True).stdout.strip()


@contextmanager
def checkout_catalog(root, temporary):
    folder = temporary / CATALOG_BRANCH
    if git(root, "ls-remote", "origin", CATALOG_REF):
        git(root, "fetch", "--no-tags", "origin", CATALOG_REF)
        tip = "FETCH_HEAD"
    else:
        tree = git(root, "mktree", stdin="")
        tip = git(root, *GIT_AUTHOR, "commit-tree", tree, "-m", "Initialize publication branch")
    git(root, "worktree", "add", "--detach", str(folder), tip)
    try:
        yield folder
    finally:
        git(root, "worktree", "remove", str(folder))


class GitHub:
    def __init__(self, repository, token):
        self.repository = repository
        self.token = token
        self.api = f"https://api.github.com/repos/{repository}"
        self.uploads = f"https://uploads.github.com/repos/{repository}"

    def request(self, path, method="GET", data=None, headers=None, host=None):
        request = Request((host or self.api) + path, data=data, headers=headers or {}, method=method)
        request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        with urlopen(request, timeout=180) as response:
            data = response.read()
        return json.loads(data) if data else None

    def write(self, path, method, data):
        return self.request(path, method, json_bytes(data), {"Content-Type": "application/json"})

    def releases(self):
        releases = []
        page = 1
        while True:
            batch = self.request(f"/releases?per_page=100&page={page}")
            releases.extend(batch)
            if len(batch) < 100:
                return releases
            page += 1

    def create_draft(self, source, component, identity):
        latest = component["latest"]
        marker = json.dumps(identity, separators=(",", ":"), ensure_ascii=True)
        body = f"{RELEASE_MARKER}{marker}{RELEASE_SUFFIX}\n\n{latest['releaseNotes']}"
        return self.write("/releases", "POST", {
            "tag_name": latest["releaseTag"], "target_commitish": source, "name": f"{component['name']} {latest['version']}",
            "draft": True, "make_latest": "false", "body": body,
        })

    def upload(self, release, archive):
        options = urlencode({"name": archive.name})
        headers = {"Content-Type": "application/zip", "Content-Length": str(archive.stat().st_size)}
        with archive.open("rb") as stream:
            return self.request(f"/releases/{release['id']}/assets?{options}", "POST", stream, headers, self.uploads)

    def publish(self, release):
        return self.write(f"/releases/{release['id']}", "PATCH", {"draft": False, "make_latest": "false"})

    def delete_asset(self, asset):
        self.request(f"/releases/assets/{asset['id']}", "DELETE")

    def delete_release(self, release):
        try:
            self.request(f"/releases/{release['id']}", "DELETE")
        except HTTPError as error:
            if error.code != 404:
                raise

    def verify_artifact(self, artifact):
        def verify():
            # Requests here intentionally use neither an API asset endpoint nor publisher credentials.
            with urlopen(artifact["url"], timeout=180) as response:
                require(response.geturl().startswith("https://"), "Release download did not remain on HTTPS")
                actual = digest_stream(response, artifact["size"])
            require(actual == (artifact["size"], artifact["sha256"]), "Anonymous ZIP size or SHA-256 mismatch")

        retry(verify, f"Verify {artifact['url']}")

    def verify_catalog(self, catalog, filename=CATALOG_FILE):
        url = f"https://raw.githubusercontent.com/{self.repository}/{CATALOG_BRANCH}/{filename}"

        def verify():
            request = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=30) as response:
                current = json.load(response, object_pairs_hook=unique_object)
            require(current == catalog, "Raw catalog has not reached the committed revision")

        retry(verify, f"Verify {url}", delays=(0, 2, 4, 8, 16, 32, 64, 128, 256))


def read_catalog(folder, repository, filename=CATALOG_FILE):
    path = folder / filename
    if not path.exists():
        require(filename == LANGUAGE_FILE or not git(folder, "ls-tree", "HEAD"), "Publication branch is missing its catalog")
        return {"schemaVersion": 1, "sourceId": SOURCE_ID, "revision": 0, "components": []}
    catalog = read_json(path)
    require(catalog["schemaVersion"] == 1 and catalog["sourceId"] == SOURCE_ID
            and type(catalog["revision"]) is int and catalog["revision"] > 0 and isinstance(catalog["publishedAt"], str),
            "Unsupported existing catalog")
    dependencies = catalog.get("dependencies", [])
    require(isinstance(dependencies, list) and len(dependencies) <= 128 and (filename == LANGUAGE_FILE or "dependencies" not in catalog), "Invalid hidden dependency pool")
    for component in catalog["components"]:
        component_id = component["id"]
        if filename == LANGUAGE_FILE:
            offers = component.get("languageOffers")
            validate_language_offers(offers)
            require("nativeVariants" not in component and component["groupId"] == "Memoria" and component["target"] == language_target(offers),
                    f"{component_id}: language metadata does not match its target")
        else:
            require(not {"languageOffers", "nativeVariants", "dependencies"} & component.keys(), f"{component_id}: typed package in ordinary catalog")
        validate_catalog_release(component, repository)
    for component in dependencies:
        component_id = component["id"]
        variants = component.get("nativeVariants")
        validate_native_variants(variants, component_id)
        require("languageOffers" not in component and component["groupId"] == "Memoria" and component["target"] == variant_target(variants),
                f"{component_id}: dependency metadata does not match its target")
        validate_catalog_release(component, repository)
    validate_unique(catalog["components"] + dependencies)
    if filename == LANGUAGE_FILE:
        validate_pins(catalog["components"], dependencies)
    return catalog


def validate_catalog_release(component, repository):
    component_id = component["id"]
    validate_catalog_component(component, component_id)
    latest = component["latest"]
    version = latest["version"]
    version_key(version)
    engine_range(latest["engineVersionRange"])
    tag = f"{component_id}-v{version}"
    require(latest["releaseTag"] == tag, f"Invalid advertised release tag for {component_id}")
    artifact = latest["artifact"]
    expected_url = f"https://github.com/{repository}/releases/download/{tag}/{component_id}-{version}.zip"
    require(artifact["url"] == expected_url and DIGEST.fullmatch(artifact["sha256"])
            and all(type(artifact[key]) is int and artifact[key] > 0 for key in ("size", "unpackedSize")),
            f"Invalid advertised artifact for {component_id}")


def validate_unique(components):
    ids = set()
    package_ids = set()
    settings = set()
    for component in components:
        component_id = component["id"]
        package_id = component["runtimePackageId"].casefold()
        settings_key = component["settingsKey"].casefold()
        require(component_id not in ids and package_id not in package_ids and settings_key not in settings,
                f"Repeated component ID, runtime package or settings key: {component_id}")
        ids.add(component_id)
        package_ids.add(package_id)
        settings.add(settings_key)


def catalog_entries(catalog):
    return catalog["components"] + catalog.get("dependencies", [])


def catalog_filename(component):
    return LANGUAGE_FILE if "languageOffers" in component or "nativeVariants" in component else CATALOG_FILE


def ordered_sources(sources):
    by_id = {descriptor["id"]: (folder, descriptor, metadata) for folder, descriptor, metadata in sources}
    visiting = set()
    processed = set()
    ordered = []

    def visit(item):
        _, descriptor, metadata = item
        component_id = descriptor["id"]
        if component_id in processed:
            return
        require(component_id not in visiting, f"Dependency cycle at {component_id}")
        visiting.add(component_id)
        for dependency_id in descriptor.get("dependencies", []):
            child = by_id.get(dependency_id)
            require(child is not None and child[1].get("kind") == "dependency", f"{component_id}: missing hidden dependency {dependency_id}")
            validate_dependency_target(metadata, child[2])
            visit(child)
        visiting.remove(component_id)
        processed.add(component_id)
        ordered.append(item)

    for item in sources:
        visit(item)
    return ordered


def native_pairs(component):
    variants = component["nativeVariants"] if "nativeVariants" in component else (pair for offer in component["languageOffers"] for pair in offer["nativeVariants"])
    return {(pair["edition"], pair["language"]) for pair in variants}


def validate_dependency_target(parent, child):
    require(native_pairs(parent) <= native_pairs(child), f"{parent['id']}: dependency {child['id']} does not cover the native profiles")


def validate_pins(roots, dependencies):
    pool = {component["id"]: component for component in dependencies}
    ordered = []
    for entry in roots + dependencies:
        pins = entry.get("dependencies", [])
        require(isinstance(pins, list) and all(isinstance(pin, dict) and set(pin) == {"id", "version"} for pin in pins),
                f"{entry['id']}: invalid dependency pins")
        ids = [pin["id"] for pin in pins]
        validate_dependency_ids(ids, entry["id"])
        for pin in pins:
            child = pool.get(pin["id"])
            require(child is not None and pin["version"] == child["latest"]["version"], f"{entry['id']}: unpublished required dependency {pin['id']}")
        descriptor = {"id": entry["id"], "dependencies": ids, "kind": "dependency" if "nativeVariants" in entry else "language"}
        ordered.append((None, descriptor, entry))
    ordered_sources(ordered)


def write_catalogs(folder, catalogs, source, component=None):
    changes = {}
    for filename, catalog in catalogs.items():
        components = {entry["id"]: entry for entry in catalog_entries(catalog)}
        if component is not None:
            component_id = component["id"]
            components.pop(RETIRED_COMPONENT_IDS.get(component_id), None)
            if filename == catalog_filename(component):
                components[component_id] = component
            else:
                components.pop(component_id, None)
        entries = [components[key] for key in sorted(components)]
        roots = [entry for entry in entries if "nativeVariants" not in entry]
        dependencies = [entry for entry in entries if "nativeVariants" in entry]
        if filename == LANGUAGE_FILE:
            versions = {entry["id"]: entry["latest"]["version"] for entry in dependencies}
            for index, entry in enumerate(roots):
                roots[index] = repin(entry, versions)
            for index, entry in enumerate(dependencies):
                dependencies[index] = repin(entry, versions)
            validate_pins(roots, dependencies)
        if not catalog["revision"] or roots != catalog["components"] or dependencies != catalog.get("dependencies", []):
            updated = {
                "schemaVersion": 1,
                "sourceId": SOURCE_ID,
                "revision": catalog["revision"] + 1,
                "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "components": roots,
            }
            if filename == LANGUAGE_FILE:
                updated["dependencies"] = dependencies
            changes[filename] = updated
    if changes:
        for filename, updated in changes.items():
            (folder / filename).write_bytes(json_bytes(updated))
        message = f"Publish {component['latest']['releaseTag']}" if component is not None else "Initialize publication catalogs"
        git(folder, "add", *changes)
        git(folder, *GIT_AUTHOR, "commit", "-m", message, "-m", f"Source: {source}")
        git(folder, "push", "origin", f"HEAD:{CATALOG_REF}")
    return {**catalogs, **changes}


def repin(component, versions):
    if not component.get("dependencies"):
        return component
    return {**component, "dependencies": [{"id": pin["id"], "version": versions.get(pin["id"])} for pin in component["dependencies"]]}


def component_releases(releases, component_id):
    tag_pattern = re.compile(rf"{re.escape(component_id)}-v([0-9]+\.[0-9]+\.[0-9]+)")
    result = {}
    for release in releases:
        match = tag_pattern.fullmatch(release["tag_name"])
        if not match:
            continue
        version = match[1]
        version_key(version)
        first_line = (release.get("body") or "").partition("\n")[0].rstrip("\r")
        require(first_line.startswith(RELEASE_MARKER) and first_line.endswith(RELEASE_SUFFIX),
                f"Unmanaged release occupies tag {release['tag_name']}")
        identity = json.loads(first_line[len(RELEASE_MARKER):-len(RELEASE_SUFFIX)], object_pairs_hook=unique_object)
        require(identity.get("componentId") == component_id and identity.get("version") == version
                and GIT_ID.fullmatch(identity.get("tree", "")) and GIT_ID.fullmatch(identity.get("sourceCommit", ""))
                and DIGEST.fullmatch(identity.get("sha256", "")),
                f"Invalid release identity for {release['tag_name']}")
        engine_range(identity["engineVersionRange"])
        require(version not in result, f"Multiple releases named {release['tag_name']}")
        result[version] = (release, identity)
    return result


def verify_release(release, filename, artifact):
    assets = release["assets"]
    require(len(assets) == 1, f"{release['tag_name']} must have exactly one asset")
    asset = assets[0]
    require(asset["name"] == filename and asset["state"] == "uploaded"
            and asset["size"] == artifact["size"] and asset.get("digest") == f"sha256:{artifact['sha256']}",
            f"Existing archive does not match reproducible bytes for {release['tag_name']}")


def select_version(releases, requested):
    if not releases:
        return requested or "1.0.0"
    major, minor, patch = max(map(version_key, releases))
    next_version = f"{major}.{minor}.{patch + 1}"
    return max((next_version, requested or next_version), key=version_key)


def ensure_candidate(host, release, identity, expected, source, archive, component):
    artifact = component["latest"]["artifact"]
    if release is None:
        release = host.create_draft(source, component, expected)
    else:
        require(identity == expected, f"Refusing to replace existing bytes or metadata for {release['tag_name']}")
    if release["draft"]:
        assets = release["assets"]
        # GitHub can leave a zero-byte starter after a failed upload.
        if len(assets) == 1 and assets[0]["name"] == archive.name and assets[0]["state"] == "starter" and assets[0]["size"] == 0:
            host.delete_asset(assets[0])
            assets = []
        if not assets:
            release["assets"] = [host.upload(release, archive)]
        verify_release(release, archive.name, artifact)
        release = host.publish(release)
    require(not release["draft"], f"Release remains a draft: {release['tag_name']}")
    verify_release(release, archive.name, artifact)
    host.verify_artifact(artifact)
    return release


def prune(host, component, keep, catalogs):
    component_id = component["id"]
    version = component["latest"]["version"]
    require(keep["tag_name"] == component["latest"]["releaseTag"] and not keep["draft"], "Cannot prune without an advertised release")
    # A cross-catalog move must be visible through both endpoints before retiring an old asset.
    for filename, catalog in catalogs.items():
        if catalog["revision"]:
            host.verify_catalog(catalog, filename)
    for candidate_version, (candidate, _) in component_releases(host.releases(), component_id).items():
        if candidate_version != version:
            retry(lambda: host.delete_release(candidate), f"Delete {candidate['tag_name']}")
    previous_id = RETIRED_COMPONENT_IDS.get(component_id)
    if previous_id:
        require(all(entry["id"] != previous_id for catalog in catalogs.values() for entry in catalog_entries(catalog)), "Cannot prune an advertised previous identity")
        for candidate, _ in component_releases(host.releases(), previous_id).values():
            retry(lambda: host.delete_release(candidate), f"Delete {candidate['tag_name']}")

    def assert_retention():
        retained = component_releases(host.releases(), component_id)
        require(set(retained) == {version} and retained[version][0]["id"] == keep["id"] and not retained[version][0]["draft"],
                f"{component_id}: publication did not retain exactly one available version")
        require(not previous_id or not component_releases(host.releases(), previous_id), "Retired identity still has published releases")

    retry(assert_retention, f"Verify release retention for {component_id}")


def report_publication(source, component, catalog):
    latest = component["latest"]
    artifact = latest["artifact"]
    print(json.dumps({"sourceCommit": source, "componentId": component["id"], "catalogRevision": catalog["revision"],
                      "version": latest["version"], "size": artifact["size"], "unpackedSize": artifact["unpackedSize"],
                      "sha256": artifact["sha256"]}), flush=True)


def validate_identity_transition(current, metadata, minimum):
    fields = ("runtimePackageId", "settingsKey")
    old = tuple(current[key] for key in fields)
    new = tuple(metadata[key] for key in fields)
    if current["id"] == metadata["id"] and old == new:
        return
    if metadata["id"] == "memoria-girl-next-door" and current["id"] == "girl-next-door":
        require(old in (("Memoria/Girl Next Door", "Girl Next Door"), ("Memoria/girl-next-door", "girl-next-door"))
                and new == ("Memoria/memoria-girl-next-door", "memoria-girl-next-door") and minimum == "[0.8.12,)",
                "The namespaced Girl Next Door identity requires Memoria 0.8.12")
        return
    require(metadata["id"] == "girl-next-door" and old == ("Memoria/Girl Next Door", "Girl Next Door")
            and new == ("Memoria/girl-next-door", "girl-next-door") and minimum == "[0.8.11,)",
            f"{metadata['id']}: cannot change stable runtime package or settings key")


def publish(root, host, resolve_minimum=latest_engine_range):
    require(not git(root, "status", "--porcelain"), "Publication requires a clean committed source checkout")
    require(not git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--", "mods/", RELEASE_CONFIG),
            "Publication cannot include ignored files in mod sources or release configuration")
    source = git(root, "rev-parse", "HEAD")
    mods = root / "mods"
    require(stat.S_ISDIR(entry_mode(mods)), "Expected a regular mods/ directory")
    folders = sorted(mods.iterdir())
    descriptors = [read_descriptor(folder) for folder in folders]
    components = [catalog_metadata(descriptor) for descriptor in descriptors]
    validate_unique(components)
    sources = ordered_sources(list(zip(folders, descriptors, components)))
    configuration = read_release_configuration(root)
    component_ids = {entry["id"] for entry in components}
    require(not any(new in component_ids and old in component_ids for new, old in RETIRED_COMPONENT_IDS.items()),
            "A retired component ID cannot be authored beside its replacement")
    require(configuration.keys() <= component_ids, "Release configuration references an unknown component ID")
    with TemporaryDirectory(prefix="memoria-mods-") as temporary, checkout_catalog(root, Path(temporary)) as catalog_folder:
        catalogs = {filename: read_catalog(catalog_folder, host.repository, filename) for filename in CATALOG_FILES}
        advertised = [entry for catalog in catalogs.values() for entry in catalog_entries(catalog)]
        validate_unique(advertised)
        existing = {entry["id"]: entry for entry in advertised}
        retained_ids = component_ids | {old for new, old in RETIRED_COMPONENT_IDS.items() if new in component_ids}
        require(existing.keys() <= retained_ids, "A cataloged component ID was removed or changed")
        releases = host.releases()
        for folder, descriptor, metadata in sources:
            component_id = descriptor["id"]
            tree = git(root, "rev-parse", f"HEAD:mods/{folder.name}")
            release_configuration = configuration.get(component_id, {})
            configured_minimum = release_configuration.get("engineVersionRange")
            if release_configuration:
                # Keep release metadata in the publication identity without rebuilding other mods.
                tree = git(root, "hash-object", "--stdin", stdin=json.dumps([tree, release_configuration], sort_keys=True))
            available = component_releases(releases, component_id)
            previous_id = RETIRED_COMPONENT_IDS.get(component_id)
            current = existing.get(component_id) or existing.get(previous_id)
            current_version = current["latest"]["version"] if current else None
            if current_version:
                current_releases = component_releases(releases, current["id"])
                require(current_version in current_releases, f"Catalog references a missing release for {component_id}")
                current_release, current_identity = current_releases[current_version]
                latest = current["latest"]
                require(not current_release["draft"] and current_identity["sha256"] == latest["artifact"]["sha256"]
                        and current_identity["engineVersionRange"] == latest["engineVersionRange"],
                        f"Catalog references a mismatched release for {component_id}")
                verify_release(current_release, f"{current['id']}-{current_version}.zip", latest["artifact"])
                validate_identity_transition(current, metadata, configured_minimum)
                if current["id"] == component_id and current_identity["tree"] == tree and catalog_filename(current) == catalog_filename(metadata):
                    if len(available) > 1 or previous_id and component_releases(releases, previous_id):
                        host.verify_artifact(latest["artifact"])
                        prune(host, current, current_release, catalogs)
                        report_publication(current_identity["sourceCommit"], current, catalogs[catalog_filename(current)])
                    continue

            matched = [version for version, (_, identity) in available.items()
                       if identity["tree"] == tree and (not current_version or version_key(version) > version_key(current_version))]
            if matched:
                version = max(matched, key=version_key)
                release, identity = available[version]
                minimum = identity["engineVersionRange"]
                require(configured_minimum is None or minimum == configured_minimum, f"{component_id}: candidate engine range differs from configuration")
                origin = identity["sourceCommit"]
            else:
                versions = set(available) | ({current_version} if current_version else set())
                version = select_version(versions, release_configuration.get("version"))
                release, identity = None, None
                origin = source
                tag = f"{component_id}-v{version}"
                require(not git(root, "ls-remote", "origin", f"refs/tags/{tag}"), f"Tag {tag} already exists without a managed release")
                minimum = configured_minimum
                if minimum is None:
                    minimum = current["latest"]["engineVersionRange"] if current else resolve_minimum()
            archive, component = build(folder, Path(temporary), descriptor, version, minimum, host.repository,
                                       release_configuration.get("releaseNotes", ""))
            if descriptor.get("dependencies"):
                versions = {entry["id"]: entry["latest"]["version"] for entry in catalogs[LANGUAGE_FILE].get("dependencies", [])}
                component["dependencies"] = [{"id": dependency, "version": versions[dependency]} for dependency in descriptor["dependencies"]]
            artifact = component["latest"]["artifact"]
            expected = {"componentId": component_id, "version": version, "tree": tree, "sourceCommit": origin,
                        "engineVersionRange": minimum, "sha256": artifact["sha256"]}
            keep = ensure_candidate(host, release, identity, expected, origin, archive, component)
            archive.unlink()
            catalogs = write_catalogs(catalog_folder, catalogs, origin, component)
            prune(host, component, keep, catalogs)
            report_publication(origin, component, catalogs[catalog_filename(component)])
        write_catalogs(catalog_folder, catalogs, source)


def main():
    repository = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
    require(repository.casefold() == REPOSITORY.casefold(), f"Expected canonical publication repository {REPOSITORY}")
    publish(Path.cwd(), GitHub(REPOSITORY, os.environ["GH_TOKEN"]))


if __name__ == "__main__":
    main()
