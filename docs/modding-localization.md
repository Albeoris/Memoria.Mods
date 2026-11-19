# Localization, fonts and encoding

[Modding reference](modding.md)

## Translate a selected installation

Open **Mods → Create Mods → New localization**. Choose the base game language for stable resource keys, the Source Reference for the prose you want to translate, and a distinct Target label. For example, choose English base scripts, Japanese source text and `Spanish (Garden edition)` as the Target. Export split JSON for version control or XLSX for a workbook. Memoria can convert a project between layouts and formats. [Create, convert and activate a project](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/players/localization.md)

Edit the `Target` in an exported row. For a tutorial sentence with `{Char Squall}`, translate the prose and keep the placeholder so it still reflects the player's character name. Preserve each `File` and `Key` plus any context bindings. For example, `bgroom_6` and `$en_bgroom_6_0000` identify a specific English field text slot. An empty Target inherits the next available text in the stack. For choices, keep the native option identities and order even if the translated heading takes a new line. [Translator guide and field controls](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/localization.md)

Keep `Localization/localization.manifest.json` and, for workbooks, any `Localization/localization.extensions.json` generated with the project. To edit text in context, choose **F2 → Localization**, select a displayed line, and preview its Target before saving. [In-game workbench](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/players/localization-workbench.md)

## Localize artwork

Use the project's `artwork` entries to find the exported Source PNG and the declared Target path. For example, reletter a selected menu graphic in the Target PNG at the same canvas size or at a uniform integer scale. Preserve the manifest's source identity, hash and geometry. Only supplying the Target for the chosen image lets the rest of the artwork inherit from the existing stack. [Structured text and artwork](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/localization.md#structured-information-and-artwork)

## Add fonts or native encoding profiles

Open **Tools → Encoding Editor** in the launcher. Create a profile, declare an atlas, and map a missing character such as `Ł` to its rectangle in your own atlas. Use the sample to check glyph advance, alignment and coverage for the intended edition, resource family and dictionary language. The Unicode glyph mapping and native byte table are separate. Change the byte table when you need to decode or encode a supported native resource with an explicit mapping. Export the profile and its declared dependencies under `Encoding/<name>/`. [Profile format, native limits and export](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/encoding-profiles.md)
