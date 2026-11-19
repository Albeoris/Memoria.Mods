# Designing reusable modding mechanisms in Memoria

Memoria's engine defines what a mod can change and how those changes interact with the game. A built-in mod supplies the same kind of data that an independent author can install. For example, the engine supports stat modifiers from active statuses, and Meltdown uses that mechanism to set how much Vit 0 reduces Vitality and Spirit. A separate package can use the same mechanism to reduce Strength while Poison is active. [Status mod reference](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/behaviors.md)

## Define the engine contract

1. **Start with the game behavior.** Identify the native resource or runtime state, the code that owns it, and the point at which it can change. Check the consumers affected by the change. For a status stat modifier, damage, Scan and AI stat queries should all read the same effective value.
2. **Reuse an existing mod format where it fits.** When a capability is missing, add the smallest typed operation that works for another valid target as well. Document what it selects, the properties and operations it supports, value ranges, units, defaults and applicable games or resource profiles. Prefer a change to one property over a replacement of the whole record when the native record can still supply the rest.
3. **Put the specific choices in the package.** Use stable game identities or semantic selectors for targets. Keep the selected field, encounter, item, status, balance values and artwork in the payload. The runtime resolves the same declarations for every package. Reserve package identity for loading, settings, dependency resolution, diagnostics and distribution. Keep package names, paths, GUIDs and hashes out of gameplay decisions.
4. **Define composition and failure behavior.** Reuse the existing discovery, enablement, settings and refresh path. Specify how omitted values inherit from the selected resources or other contributions, and how overlapping writes are resolved. Use established precedence for simple replacements and explicit ownership or conflict checks when edits depend on one another. Validate versions, selectors, values and resource compatibility before publishing a candidate. Report the package, document and target responsible for an invalid contribution, and retain the last accepted state at the relevant publication boundary.
5. **Apply the result at the native lifecycle boundary.** Give gameplay the resolved data through the existing owner. Preserve native timing, story progression, rewards and other behavior outside the declared change. If an encounter captures its rules when it starts, refresh takes effect at the next supported entry or restart. Save enough state to resume an active instance with its accepted rules. Preserve older saves using their recorded facts and documented defaults where the contract allows.

## Author the built-in mod

Give the package a stable native descriptor ID and use the normal payload layout. Put default values and editable option descriptions in the authored data. Use the ordinary settings and enablement mechanisms for player choices. For a download of a bundled mod, preserve its identity and authoring format so that the installer can retain the player's settings. Use the publishing workflow to express the minimum engine version and generate the distribution metadata. [Descriptor and package formats](../formats.md) · [Publishing](../README.md#publishing)

These existing mods illustrate where to draw the boundary:

| Mod | Reusable engine capability | Package contribution |
| --- | --- | --- |
| Meltdown | Project supported stat modifiers from active statuses. | Declare the modifiers and sliders for Vit 0. [Payload](../mods/meltdown/statuses/vit0.json) |
| Depixelation | Hide selected background groups and bind an existing field actor to a presentation model and animation. | Select Cid's background tiles and model binding while his original object retains scripts and interaction. [Field mod reference](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/field-mods.md) |
| Edea — Mirrors | Compose typed encounter operations against a semantic encounter binding. | Declare the mirror summons, turn rules and damage routes for the Disc 1 encounter. The original encounter retains story and result ownership. [Encounter mod reference](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/encounters.md) |
| Girl Next Door | Override item properties and open a package image through the generic item-use action. | Select the magazine and supply its image. [Item override reference](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/item-overrides.md) |
| FFVII Enemy Integration | Apply property deltas to enemy records from the selected FFVII resources. | Supply Scan text, growth, Draw and reward changes for selected enemy IDs. [FFVII enemy extension reference](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/ff7-enemy-extensions.md) |

## Verify the contract through the normal mod path

- Run the built-in payload through normal discovery, preparation and gameplay. Disable it and verify that the selected game resources and remaining enabled mods determine behavior.
- Author a small independent package that uses the same operation on another supported target or supplies a different value. Verify that it exercises the same loader, settings and runtime path.
- Cover independent edits, overlapping edits and malformed or unsupported input as the format permits. Check deterministic resolution, actionable diagnostics and the state retained after a failed refresh.
- Check the lifecycle boundaries the capability actually affects. For a battle rule, this can include entry with the selected resources, an action that consumes the rule, and restoration from a snapshot. For an install or upgrade, check that the payload is discovered and player settings survive.
