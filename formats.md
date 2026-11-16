# Official metadata formats

The catalog at `catalog/catalog-v1.json` on GitHub has schema version `1`, source ID `memoria-official`, a numeric `revision`, a UTC `publishedAt` timestamp, and a `components` array. The authoring descriptor provides each component's stable ID, group ID, runtime package ID, settings key, name, description, category, format, delivery, default activation and game target. Each component's `latest` record contains the resolved version, engine version range, release tag, release notes and artifact metadata.

Each artifact has a direct HTTPS Release URL, its ZIP's byte `size`, `unpackedSize` (the sum of all archive entries' uncompressed sizes), and its lowercase hexadecimal `sha256`. An official versioned tag is `<id>-v<version>` and the single asset for that release is `<id>-<version>.zip`.

The ZIP contains `mod.manifest.json` at its root, the actual files under `payload/`, and an optional root `NOTICE.md`. The manifest contains `schemaVersion`, `sourceId`, `componentId`, `runtimePackageId`, `version`, `engineVersionRange`, `target`, and `files`. Every file record contains its payload-relative `path` with slash separators, byte `size`, lowercase `sha256`, and Boolean `mutable`. The manifest inventory covers payload files. The installer uses mutable defaults when preserving users' existing settings.

Targets declare the game `FFVIII`, applicable `editions` and `languages`. Use `["*"]` for language-neutral mods. The publisher accepts normalized NuGet engine ranges with an inclusive minimum and no upper bound.

Both generated documents conform to [the version 1 JSON Schema](schemas/official-v1.schema.json).
