# Official metadata formats

The catalog at `catalog/catalog-v1.json` on GitHub has schema version `1`, source ID `memoria-official`, a numeric `revision`, a UTC `publishedAt` timestamp, and a `components` array. The publisher derives each component's metadata from `mod.json`. `package` supplies `groupId` and `category`, `id` supplies `settingsKey` and `Memoria/<id>` supplies `runtimePackageId`. The catalog uses the English description. Official packages declare format `memoria-native`, delivery `download`, and `defaultEnabled: true`. The engine applies the player's activation preferences. Each component's `latest` record contains the resolved version, engine version range, release tag, release notes and artifact metadata.

Each artifact has a direct HTTPS Release URL, its ZIP's byte `size`, `unpackedSize` (the sum of all archive entries' uncompressed sizes), and its lowercase hexadecimal `sha256`. An official versioned tag is `<id>-v<version>` and the single asset for that release is `<id>-<version>.zip`.

The ZIP contains `mod.manifest.json` at its root, the mod's source files including `mod.json` under `payload/`, and an optional root `NOTICE.md`. The manifest contains `schemaVersion`, `sourceId`, `componentId`, `runtimePackageId`, `version`, `engineVersionRange`, `target`, and `files`. Every file record contains its payload-relative `path` with slash separators, byte `size`, lowercase `sha256`, and `mutable` set to `false`. The manifest inventory covers payload files. The player controls activation through local settings. The publisher rejects any payload path containing a `mod.ini` segment, regardless of casing.

Ordinary official packages target `FFVIII` Classic in every language (`["*"]`). The publisher accepts normalized NuGet engine ranges with an inclusive minimum and no upper bound.

Both generated documents conform to [the version 1 JSON Schema](schemas/official-v1.schema.json).

## Language catalog

The separate `catalog/languages-v1.json` index uses the same envelope, release data and download format. A language pack remains one component, with one asset, receipt and stable package identity. Its component uses `groupId: "Memoria"`, `category: "Localization"`, `defaultEnabled: false` and includes one or more `languageOffers`. Each offer declares a canonical display language tag, an edition label and a stable package option. The option can be a native Target label such as `ru (Literary edition)`.

Each offer declares specific edition and base-language pairs as `nativeVariants`. Clients match those pairs. The component and ZIP manifest targets summarize the union of editions and languages across offers for the existing installer. Use `ja` in catalog metadata for Japanese. Follow the [language catalog schema](schemas/languages-v1.schema.json).

```json
{
  "kind": "language",
  "package": "Localization",
  "languageOffers": [
    {
      "locale": "ru",
      "label": "FFRTT",
      "option": "ru",
      "nativeVariants": [
        { "edition": "Classic", "language": "en" },
        { "edition": "Remastered", "language": "en" }
      ]
    }
  ]
}
```

Add these fields alongside the normal descriptor fields. A language or dependency descriptor can list required package IDs in `dependencies`. A required package declares `kind: "dependency"`, `package: "Localization"` and its own `nativeVariants`. It appears in the language index's top-level `dependencies` pool. The publisher records exact `{id, version}` pins in each dependent component and updates them together with the dependency release. The installer resolves the complete dependency graph before switching the chosen language. Each dependency keeps its own verified version and package receipt.

FFRTT owns the opening subtitles and defines the movie component with `mediaDependency: "russian-ffrtt-opening"`. The dependency owns the video and matching camera, so a translation or subtitle update can reuse their installed release.

A language release moves between indexes in a single publication commit if its type changes. The two indexes maintain independent revisions.

## Mod descriptor

`mod.json` declares the stable lowercase hyphenated `id`, the display `name`, the display group in `package`, and a `description` object keyed by language code with an `en` entry. Mod IDs are globally unique among native mods; `package` is not an ID namespace. New Memoria-authored IDs begin with `memoria-`. Released third-party projects retain their existing IDs. Changing `package` moves the mod to the new group while retaining its identity. The installed descriptor includes every authored translation. Write descriptor and item documents as UTF-8 JSON without a byte order mark.

## Status overrides

Place one status definition in each JSON file under `statuses/`. The engine discovers these files recursively; the filename has no identity meaning. The native status `id` selects the target. Each entry in `statModifiers` supplies an `operation` and `value` for one supported stat. A numeric value is fixed; an object with `default` exposes a slider and can supply optional `min`, `max`, localized `label` and localized `description`. `ReducePercent` currently supports 0 through 100, which is also the default slider range. The optional status `name` uses the existing localized text-reference format.

See the [Meltdown payload](mods/meltdown/statuses/vit0.json) and the [engine's status authoring guide](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/behaviors.md). Both bundled and downloaded mods use the same loader and settings identity.

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

Use each integer `id` from 1 through 255 at most once per package. Set `type` to `Magazine`, set `use` to a `ShowImage` action, or set both. A `ShowImage` action opens a PNG with the item name as its caption. Image paths without a leading slash resolve from the item document's directory. A leading `/` resolves from the package root. Use forward slashes, match the file's casing, and keep the path inside the package. The publisher checks PNG structure and size before packaging. Enabled packages apply normal mod priority to overlapping overrides.
