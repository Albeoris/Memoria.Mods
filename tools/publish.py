"""Reconcile the public catalog and releases with a committed main checkout."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from build import (COMPONENT_ID, REPOSITORY, SOURCE_ID, build, digest_stream, engine_range, json_bytes, latest_engine_range,
                   read_descriptor, read_json, require, unique_object, validate_descriptor, version_key)


CATALOG_BRANCH = "catalog"
CATALOG_FILE = "catalog-v1.json"
RELEASE_MARKER = "<!-- memoria-official-v1:"
RELEASE_SUFFIX = " -->"
DIGEST = re.compile(r"[a-f0-9]{64}")
GIT_ID = re.compile(r"[a-f0-9]{40}")
CATALOG_REF = f"refs/heads/{CATALOG_BRANCH}"
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

    def verify_catalog(self, catalog):
        url = f"https://raw.githubusercontent.com/{self.repository}/{CATALOG_BRANCH}/{CATALOG_FILE}"

        def verify():
            request = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=30) as response:
                current = json.load(response, object_pairs_hook=unique_object)
            require(current == catalog, "Raw catalog has not reached the committed revision")

        retry(verify, f"Verify {url}", delays=(0, 2, 4, 8, 16, 32, 64, 128, 256))


def read_catalog(folder, repository):
    path = folder / CATALOG_FILE
    if not path.exists():
        require(not git(folder, "ls-tree", "HEAD"), "Publication branch is missing its catalog")
        return {"schemaVersion": 1, "sourceId": SOURCE_ID, "revision": 0, "components": []}
    catalog = read_json(path)
    require(catalog["schemaVersion"] == 1 and catalog["sourceId"] == SOURCE_ID
            and type(catalog["revision"]) is int and catalog["revision"] > 0 and isinstance(catalog["publishedAt"], str),
            "Unsupported existing catalog")
    validate_unique(catalog["components"])
    for component in catalog["components"]:
        component_id = component["id"]
        validate_descriptor(component, component_id)
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
    return catalog


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


def write_catalog(folder, catalog, component, source):
    components = {entry["id"]: entry for entry in catalog["components"]}
    components[component["id"]] = component
    updated = {
        "schemaVersion": 1,
        "sourceId": SOURCE_ID,
        "revision": catalog["revision"] + 1,
        "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "components": [components[key] for key in sorted(components)],
    }
    (folder / CATALOG_FILE).write_bytes(json_bytes(updated))
    git(folder, "add", CATALOG_FILE)
    git(folder, *GIT_AUTHOR, "commit", "-m", f"Publish {component['latest']['releaseTag']}", "-m", f"Source: {source}")
    git(folder, "push", "origin", f"HEAD:{CATALOG_REF}")
    return updated


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


def prune(host, component, keep, catalog):
    component_id = component["id"]
    version = component["latest"]["version"]
    require(keep["tag_name"] == component["latest"]["releaseTag"] and not keep["draft"], "Cannot prune without an advertised release")
    host.verify_catalog(catalog)
    for candidate_version, (candidate, _) in component_releases(host.releases(), component_id).items():
        if candidate_version != version:
            retry(lambda: host.delete_release(candidate), f"Delete {candidate['tag_name']}")

    def assert_retention():
        retained = component_releases(host.releases(), component_id)
        require(set(retained) == {version} and retained[version][0]["id"] == keep["id"] and not retained[version][0]["draft"],
                f"{component_id}: publication did not retain exactly one available version")

    retry(assert_retention, f"Verify release retention for {component_id}")


def report_publication(source, component, catalog):
    latest = component["latest"]
    artifact = latest["artifact"]
    print(json.dumps({"sourceCommit": source, "componentId": component["id"], "catalogRevision": catalog["revision"],
                      "version": latest["version"], "size": artifact["size"], "unpackedSize": artifact["unpackedSize"],
                      "sha256": artifact["sha256"]}), flush=True)


def publish(root, host, resolve_minimum=latest_engine_range):
    require(not git(root, "status", "--porcelain"), "Publication requires a clean committed source checkout")
    require(not git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--", "mods/"),
            "Publication cannot include ignored files in mod source folders")
    source = git(root, "rev-parse", "HEAD")
    folders = sorted((root / "mods").iterdir())
    descriptors = [read_descriptor(folder) for folder in folders]
    validate_unique(descriptors)
    with TemporaryDirectory(prefix="memoria-mods-") as temporary, checkout_catalog(root, Path(temporary)) as catalog_folder:
        catalog = read_catalog(catalog_folder, host.repository)
        existing = {entry["id"]: entry for entry in catalog["components"]}
        require(existing.keys() <= {entry["id"] for entry in descriptors}, "A cataloged component folder was removed or renamed")
        releases = host.releases()
        for folder, descriptor in zip(folders, descriptors):
            component_id = descriptor["id"]
            tree = git(root, "rev-parse", f"HEAD:mods/{component_id}")
            available = component_releases(releases, component_id)
            current = existing.get(component_id)
            current_version = current["latest"]["version"] if current else None
            if current_version:
                require(current_version in available, f"Catalog references a missing release for {component_id}")
                current_release, current_identity = available[current_version]
                latest = current["latest"]
                require(not current_release["draft"] and current_identity["sha256"] == latest["artifact"]["sha256"]
                        and current_identity["engineVersionRange"] == latest["engineVersionRange"],
                        f"Catalog references a mismatched release for {component_id}")
                verify_release(current_release, f"{component_id}-{current_version}.zip", latest["artifact"])
                for key in ("runtimePackageId", "settingsKey", "groupId"):
                    require(current[key] == descriptor[key], f"{component_id}: cannot change stable {key}")
                if current_identity["tree"] == tree:
                    if len(available) > 1:
                        host.verify_artifact(latest["artifact"])
                        prune(host, current, current_release, catalog)
                        report_publication(current_identity["sourceCommit"], current, catalog)
                    continue

            matched = [version for version, (_, identity) in available.items()
                       if identity["tree"] == tree and (not current_version or version_key(version) > version_key(current_version))]
            if matched:
                version = max(matched, key=version_key)
                release, identity = available[version]
                minimum = identity["engineVersionRange"]
                origin = identity["sourceCommit"]
            else:
                version = select_version(available, descriptor.get("version"))
                release, identity = None, None
                origin = source
                tag = f"{component_id}-v{version}"
                require(not git(root, "ls-remote", "origin", f"refs/tags/{tag}"), f"Tag {tag} already exists without a managed release")
                minimum = descriptor.get("engineVersionRange")
                if minimum is None:
                    minimum = current["latest"]["engineVersionRange"] if current else resolve_minimum()
            archive, component = build(folder, Path(temporary), descriptor, version, minimum, host.repository)
            artifact = component["latest"]["artifact"]
            expected = {"componentId": component_id, "version": version, "tree": tree, "sourceCommit": origin,
                        "engineVersionRange": minimum, "sha256": artifact["sha256"]}
            keep = ensure_candidate(host, release, identity, expected, origin, archive, component)
            archive.unlink()
            catalog = write_catalog(catalog_folder, catalog, component, origin)
            prune(host, component, keep, catalog)
            report_publication(origin, component, catalog)


def main():
    repository = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
    require(repository.casefold() == REPOSITORY.casefold(), f"Expected canonical publication repository {REPOSITORY}")
    publish(Path.cwd(), GitHub(REPOSITORY, os.environ["GH_TOKEN"]))


if __name__ == "__main__":
    main()
