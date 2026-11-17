# Official metadata formats

The catalog at `catalog/catalog-v1.json` on GitHub has schema version `1`, source ID `memoria-official`, a numeric `revision`, a UTC `publishedAt` timestamp, and a `components` array. The publisher derives each component's metadata from `mod.json`. `package` supplies `groupId`, `id` supplies `settingsKey` and `Memoria/<id>` supplies `runtimePackageId`. The catalog uses the English description. Official item packages declare category `Items`, format `memoria-native`, delivery `download`, and `defaultEnabled: true`. The engine applies the player's activation preferences. Each component's `latest` record contains the resolved version, engine version range, release tag, release notes and artifact metadata.

Each artifact has a direct HTTPS Release URL, its ZIP's byte `size`, `unpackedSize` (the sum of all archive entries' uncompressed sizes), and its lowercase hexadecimal `sha256`. An official versioned tag is `<id>-v<version>` and the single asset for that release is `<id>-<version>.zip`.

The ZIP contains `mod.manifest.json` at its root, the mod's source files including `mod.json` under `payload/`, and an optional root `NOTICE.md`. The manifest contains `schemaVersion`, `sourceId`, `componentId`, `runtimePackageId`, `version`, `engineVersionRange`, `target`, and `files`. Every file record contains its payload-relative `path` with slash separators, byte `size`, lowercase `sha256`, and `mutable` set to `false`. The manifest inventory covers payload files. The player controls activation through local settings. The publisher rejects any payload path containing a `mod.ini` segment, regardless of casing.

Official item packages target `FFVIII` Classic in every language (`["*"]`). The publisher accepts normalized NuGet engine ranges with an inclusive minimum and no upper bound.

Both generated documents conform to [the version 1 JSON Schema](schemas/official-v1.schema.json).

## Mod descriptor

`mod.json` declares the stable lowercase hyphenated `id`, the display `name`, the `package` (`Memoria` for official packages), and a `description` object keyed by language code with an `en` entry. The installed descriptor includes every authored translation. Write descriptor and item documents as UTF-8 JSON without a byte order mark.

## Item overrides

Put one item override in each JSON document under `items/`. Subdirectories and filenames organize the files. The document's `id` identifies the item.

```json
{
  "id": 163,
  "type": "Magazine",
  "use": {
    "action": "ShowImage",
    "image": "./163-girl-next-door.png"
  }
}
```

Use each integer `id` from 0 through 255 at most once per package. Set `type` to `Magazine`, set `use` to a `ShowImage` action, or set both. A `ShowImage` action opens a PNG with the item name as its caption. Image paths without a leading slash resolve from the item document's directory. A leading `/` resolves from the package root. Use forward slashes, match the file's casing, and keep the path inside the package. Enabled packages apply normal mod priority to overlapping overrides.
