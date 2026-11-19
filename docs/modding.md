# Memoria FFVIII modding reference

Choose the format that matches the part of the game you want to change. The examples use Classic resources unless a guide identifies another supported profile. Check the linked engine reference for the required build and resource profile. [Supported editions](https://github.com/Albeoris/Memoria.FF8#about)

| Goal | Authoring guide |
| --- | --- |
| Change what a GF learns, how a status affects stats, or what an item does in the menu | [Gameplay, battles and inventory](modding-gameplay.md) |
| Rework a scene, light a room, repaint a texture, or edit a model and its animations | [Fields, lighting, textures and models](modding-visuals.md) |
| Translate text and artwork, add glyphs, or define native byte mappings | [Localization, fonts and encoding](modding-localization.md) |
| Install an existing mod or replace native data, movies, music or voices | [Junction, FFNx, Tonberry and Demaster compatibility](modding-compatibility.md) |

## Start a native package

Create a folder such as `Mods/Memoria/atelier-example/` under the installed game's `Mods` directory or the launcher's adjacent `Mods` directory. Put `mod.json` at the package root. A direct game launch can select an alternate root with `--mods-dir <directory>`. If the same package is installed in both roots, the game-directory copy takes precedence. [Native package discovery](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/field-mods.md#install-and-configure)

```json
{
  "id": "atelier-example",
  "name": "Atelier Example",
  "package": "Atelier",
  "description": {
    "en": "A small example mod for Memoria FFVIII."
  }
}
```

Keep the lowercase `id` globally unique and stable. Use `package` to group components in the Mods menu and provide `en` as the description fallback. Save JSON as UTF-8 without a byte order mark. Add a payload from one of the authoring guides to make the example functional. [Descriptor and distribution formats](../formats.md)

## Check the mod in game

1. Open **Mods** and check that the package is present and enabled. You can also open the mod manager from **F2 → Mods**. New packages inherit the automatic enablement preference.
2. After changing an installed file, select **Refresh** and check the package details for diagnostics. The payload type determines when the change takes effect. For example, a status change affects newly started battles, and an acquired binary replacement can require a restart.
3. Exercise the feature with the package enabled, then disable it to compare the result with your normal setup. When several mods target the same resource, check the format's precedence and conflict rules. A local `mod.ini` can store `enabled` and, for formats that use numeric priority, `priority`. [Enablement, ordering and refresh](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/texture-mods.md#install-and-configure)

Keep the player's `mod.ini` out of distribution archives. For this repository's official ZIP and catalog workflow, see [Publishing](../README.md#publishing).
