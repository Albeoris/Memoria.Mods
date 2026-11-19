# Gameplay, battles and inventory

[Modding reference](modding.md)

## Status effects

Put one document per existing status under `statuses/`. This `statuses/poison.json` gives Poison a 20% Strength penalty while the status is active. `ReducePercent` supports `Strength`, `Vitality` and `Spirit`. A fixed integer sets the reduction directly. An object with `default`, optional bounds and localized labels or descriptions adds a player slider. The game resolves one contribution per status and stat at battle entry. [Status definitions and settings](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/behaviors.md) · [Meltdown payload](../mods/meltdown/statuses/vit0.json)

```json
{
  "id": "Poison",
  "statModifiers": {
    "Strength": { "operation": "ReducePercent", "value": 20 }
  }
}
```

The bundled Level-Based Stat Bonuses component changes how the current level contributes to equipped HP, Str, Vit, Mag and Spr Bonus abilities. Use the built-in toggle to try that rule. [Stat bonus sources and behavior](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/stat-bonuses.md)

## Items and illustrations

Use `items/<name>.json` to change an existing item's type to `Magazine`, give it a `ShowImage` use action, or do both. This example opens `garden-map.png` beside the JSON when the item is used from the field or world item menu. Supply your own PNG. The use action preserves item stock. Specify each item ID once per package. [Item authoring guide](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/item-overrides.md) · [Girl Next Door payload](../mods/girl-next-door/items/163-girl-next-door.json)

```json
{
  "id": 163,
  "type": "Magazine",
  "use": { "action": "ShowImage", "image": "./garden-map.png" }
}
```

## GF learn lists

Open **Mods → Create Mods → GF Abilities**. Select a GF, add an existing ability from the catalog, then use **Preview** to inspect the new learn list and **Save mod** to create the package. A package can add or replace abilities, change their initial learned state, or reorder a subset. Its `gameplay/gf-abilities.json` targets stable GF and ability IDs. Earned AP and learned state are managed separately from the authored starting defaults. [GF editor guide](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/players/gf-ability-editor.md) · [Native learn-list format](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/gf-abilities.md)

## GF AP costs, descriptions and summon damage limits

Starting with Memoria FFVIII 0.8.15, a native package can publish `gameplay/gf-rules.json`. Use `defaultSummonDamageLimit` for a global summon cap and entries in `abilities` to customize existing ability IDs. Each entry can change `apCost`, supply a localized `description` with an English fallback, or set the `summonDamageLimit` for a GF that has learned that ability. Use IDs 1–115 and AP costs 1–255. Damage limits accept positive integers or `"unlimited"`.

This example sets the base limit to 9,999 and raises the AP cost of Boost (ability 91) to 200. Learning Boost removes the native FFVIII summon damage cap for that GF. [Boost Damage Limit payload](../mods/boost-damage-limit/gameplay/gf-rules.json)

```json
{
  "schemaVersion": 1,
  "defaultSummonDamageLimit": 9999,
  "abilities": [
    {
      "id": 91,
      "apCost": 200,
      "description": { "en": "Boost GF damage and remove the summon damage cap." },
      "summonDamageLimit": "unlimited"
    }
  ]
}
```

The largest applicable learned-ability limit wins, with `"unlimited"` above every numeric limit. A GF with no learned ability that declares a limit uses the package baseline if supplied, or the native limit. FFVII guest summon damage keeps its FFVII limit. Disjoint property changes compose, and multiple writes to the same property are reported as conflicts. Use the learn-list format to assign an ability to a particular GF.

## Semantic FFVIII encounters

Use `gameplay/encounters/*.json` to adjust an encounter with a supported binding. The Classic Disc 1 Edea binding is `ff8.disc1.edea`. This document raises the camera by 8 degrees when the current elevation is 0. The same format supports guarded HP and Reflect-resistance edits, summons, normal-action rules, damage routing, presentation tracks and the bound native taunt gate. The game captures the compiled encounter at battle entry. [Encounter selectors, operations and composition](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/encounters.md)

```json
{
  "schemaVersion": 1,
  "documentId": "atelier.edea-camera",
  "encounter": "ff8.disc1.edea",
  "sourceProfile": "Classic",
  "atomic": true,
  "operations": [
    {
      "id": "raise-camera",
      "kind": "SetCameraProperty",
      "property": "elevationDegrees",
      "expectedValue": 0,
      "value": 8
    }
  ]
}
```

## FFVII crossover encounters

Put enemy property changes under `ff7/gameplay/enemy-extensions/`. An entry can supply Scan prose, change level scaling or growth, add FFVIII Draw tiers, or adjust the source enemy's rewards and item replacements. This example changes only the Scan description for enemy 105. Independent properties compose. Two contributions to the same Scan description cause a conflict. [FFVII enemy extension format](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/ff7-enemy-extensions.md)

```json
{
  "schemaVersion": 1,
  "documentId": "atelier.ember-scan",
  "enemies": [
    {
      "enemyId": 105,
      "scanDescription": "This living ember swells as the fight goes on."
    }
  ]
}
```
