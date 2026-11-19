# Fields, lighting, textures and models

[Modding reference](modding.md)

## Field scenes and movement

Put a patch under `fields/<fieldName>.json` and identify the scene by both its name and numeric ID. Inspect the field through **F2 → Scene inspector** to find the native background groups and script objects. You can hide or show groups of tiles, narrow a selection by region and depth, or bind an existing actor to a different field model and animation. The original object still owns its scripts and interactions. [Scene selectors and actor bindings](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/field-mods.md)

For example, save this as `fields/ggkodo1.json` to rotate player input in the Galbadia Garden lobby. Check the result with a keyboard or stick. An optional `cameraIndex` restricts the change to one field camera. The bundled Reorientation component also targets this lobby, so compare in isolation or check which package supplies the active movement value. [Movement projection](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/field-mods.md#movement-projection)

```json
{
  "schemaVersion": 1,
  "fieldId": 687,
  "fieldName": "ggkodo1",
  "movement": { "rotationDegrees": -47.8125 }
}
```

## Field lighting

Use **Mods → Create → Create lighting mod**, then visit a field and press **Ctrl+L**. Add a warm spotlight above an actor, add a dim fill, and save with **Ctrl+S**. The editor can aim spotlights, select the Primary shadow source and add floor or wall shadow receivers. Enable the selected mod for normal play after reviewing the result. [Lighting controls and format](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/field-lighting.md)

The editor writes `lighting/component.json` and per-field documents under `lighting/fields/`. Normal play selects one lighting component by package precedence. Its fields without authored lights use native lighting. [Lighting package selection](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/field-lighting.md#package-format)

## Native textures

Open **F2 → Textures** and export a selected portrait as a PNG. Repaint it at the source proportions, preserve its path and companion metadata, then use **Import files** to save the changed image into `Mods/Memoria`. The authoring tool shows Original, Resolved and imported artwork for comparison. For field backgrounds it exports layered `.ora` documents with source companions. Preserve the layers and their native tile identities while painting. [Texture export, import and package paths](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/texture-authoring.md)

## Models and animations

Open **F2 → Model browser**, select a field character or battle model and export its GLB for Blender. Try changing one accessory or one animation clip. Keep Blender's **Custom Properties** enabled so the surviving content retains its Memoria destination identities. Import the GLB as a mod and review each matched geometry or clip slot before saving. The importer validates the model's rig and clip contracts against the selected installation. [Model browser and Blender workflow](https://github.com/Albeoris/Memoria.FF8/blob/main/docs/modding/model-mods.md)
