# Memoria FFVIII official mods

Browse and install these mods from the Mods menu in [Memoria FFVIII](https://github.com/Albeoris/Memoria.FF8). Girl Next Door is available for Classic in every supported language and requires Memoria FFVIII 0.8.11 or later.

## Publishing

1. Put each independently published mod in its own `mods/` subfolder. Copy the [Girl Next Door descriptor](mods/girl-next-door/mod.json) as a starting point. Choose a lowercase hyphenated ID and keep it stable after publication.
2. Put redistributable native resources beside `mod.json` or in its subdirectories. The player's local `mod.ini` is reserved for their settings. Keep it, original game assets and engine binaries out of the package. Put any required attribution in `NOTICE.md` beside `mod.json`.
3. Set `engineVersionRange` to `[minimum,)` in [releases.json](releases.json) under the descriptor ID if the package needs a specific engine version. When omitted, the first release uses the latest stable Memoria FFVIII release and subsequent versions inherit the published minimum.
4. Commit and push to `main`. Changes to a mod's files or release configuration publish independently, starting at `1.0.0` and incrementing the patch version automatically. Set the optional `version` in `releases.json` to publish at a higher stable version.

The workflow runs serially and checks the current `main` commit against each published folder. It verifies the versioned Release ZIP over anonymous HTTPS, switches the [public catalog](https://raw.githubusercontent.com/Albeoris/Memoria.Mods/catalog/catalog-v1.json), then removes superseded releases for that mod. Rerun **Publish official mods** from `main` after an interruption. The workflow reuses matching drafts or published candidates and retries unfinished cleanup. It keeps one advertised and one published release per mod after success.

## Local validation

```sh
python tools/build.py mods/girl-next-door
python -m unittest discover -s tools
```

The builder writes a reproducible ZIP and catalog entry under `artifacts/`. Pass `--version` and `--engine-version-range` to reproduce the resolved fields of a published package. The catalog and package formats are described in [formats.md](formats.md).

To open a PNG from the game's item menu, give the item a `ShowImage` action. See [the item override format](formats.md#item-overrides) for the file structure and image paths.
