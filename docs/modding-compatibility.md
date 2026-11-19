# Junction, FFNx, Tonberry and Demaster compatibility

[Modding reference](modding.md)

Memoria reads these package formats through its own adapters. Match the address layout, edition and target family in the engine reference before choosing a format. For newly painted textures, the Memoria texture import tool can write a native package directly. [Native texture authoring](modding-visuals.md#native-textures)

| Format | Install under | Example use |
| --- | --- | --- |
| Junction | `Mods/Junction/` | Read `.iroj` archives or unpacked `mod.xml` packages with static options, recognized resource paths and manifest ordering. For example, choose a declared artwork variant with `option.texture_variant = 2` in the package's settings. |
| FFNx through Junction | `Mods/Junction/` | Address supported Classic textures using the `mods/textures/` identities published by the selected source. Selected `FFNxConfig` options can configure external music or a video extension. |
| Tonberry | `Mods/Tonberry/<package>/` | Install an unpacked Classic texture pack with its hash tables. Supported source identities include Classic world TEXL pages and road regions. |
| Demaster | `Mods/Demaster/<package>/` | Supply textures for a supported source layout, such as card artwork on 256 × 256 source canvases or field background pages. The registered GF atlas layout requires a supporting Remastered resource source. |

[Texture formats and supported layouts](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/texture-mods.md) · [Junction manifests and options](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/file-replacements.md#manifest-and-configuration)

## Classic native data and encoded assets through Junction

Recognized resource families include kernel, initialization and dictionary data, battle formations and actors, menu groups and tables, world data, model resources, individual field components and complete field archives. Indexed text replacements include card names, Scan and draw-point tables. Each consumer has its own validation contract. For example, a `direct/exe/card_names.msd` override supplies the complete ordered table of 110 card names. Changing an acquired encoded asset may require a game restart. [Registered file replacements](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/file-replacements.md)

## Classic media through Junction

- **Music.** Place an Ogg track at `music/<track>.ogg`, using the part of the native filename after the first hyphen. For example, give one town a new arrangement and tune its volume in `music/config.toml`. Configured PSF and MiniPSF paths can also supply music when the required BIOS and library dependencies are available. [External music](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/file-replacements.md#external-music)
- **Dialogue voices.** Record a field line and save the first page as `voice/<field>/<message>a.ogg`, with the native message number. You can use a window-specific filename such as `w2_3a.ogg` for message 3 in window 2. [Field voice naming and settings](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/file-replacements.md#field-dialogue-voices)
- **Movies.** Replace a movie at its requested address. Supply any presentation CAM from the same package and preserve the original script timing and CAM flags. A configured video extension can select an alternate container such as MP4. [Movie and CAM timing](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/file-replacements.md#movie-lifetime-and-timing)

## Reviewed startup options

Junction can map individually reviewed HEXT payloads to engine startup behaviors such as skipping the publisher movie, logo or credits images. The import contract uses exact approved hashes and the reviewed executable profile. Manual startup skipping is an intrinsic engine feature. [Reviewed startup behavior](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/file-replacements.md#reviewed-startup-behavior)
