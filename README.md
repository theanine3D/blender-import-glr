# blender-import-glr
Addon which adds glr import support to Blender, the free 3D modelling suite.

This is a fork of Luctaris's [original addon](https://github.com/Luctaris/blender-import-glr), which is no longer being maintained by the original developer. My fork of this addon maintains support for Blender 4.3 and higher, and also adds an Emission shader node to the imported GLR materials. The latter makes it possible for my addon [Material Batch Tools](https://extensions.blender.org/add-ons/matbatchtools/) to easily swap the Emission node with a Principled node for all materials, if you prefer to use Blender light instead of vertex color lighting for your scene.

---

## About

Allows Blender 3.5+ to import GLR scene files, a custom binary file format developed and generated by [GLideN64-SceneRipper](https://github.com/Luctaris/GLideN64-SceneRipper/) for ripping triangle information from N64 games.

## Examples

<picture>
  <img width=854 height=480 alt="The Legend of Zelda: Majora's Mask" src="pictures/majoras_mask.png">
</picture>

<picture>
  <img width=854 height=480 alt="GoldenEye 007" src="pictures/goldeneye.png">
</picture>

<picture>
  <img width=854 height=480 alt="Starfox 64" src="pictures/starfox.png">
</picture>

<picture>
  <img width=854 height=480 alt="Banjo-Tooie" src="pictures/tooie.png">
</picture>

## Installation

Download the `io_import_glr` folder from this repo and place it into your Blender's addon folder `(Main Blender folder)/(Version)/scripts/addons/`.

You can also try to install the .zip file (in Releases) as a Blender addon via the preferences panel.

## General Usage

1. Open Blender
2. Go to `File->Import->GLideN64 Rip (.glr)`
3. Configure import options on the right-side panel provided by the importer.
- Config explanations listed below
4. (Optional) Select and highlight one or more textures to add to the blacklist/whitelist
5. Select and highlight one or more .glr files to import
6. Import!

## Blacklist Usage

To assist with specifying a blacklist/whitelist for imported files, I added in an operator create a list for you.

1. Go into edit mode on an imported scene
- Note: This operator only displays and works in edit mode!
2. Select the faces containing the material you'd like to blacklist/whitelist
3. Press F3 (or your configured hotkey) to bring up the operator search menu
4. Search for `filter` and you should see an option for `Generate Texture Filter List`
5. Upon usage, you should see a confirmation of the list being generated towards the bottom of your screen.
6. Generated texture list should be copied into your clipboard. You can now paste it into the `Textures` box on next import.
7. (optional) Check your Blender text editor for an entry named `selected_textures` if you want to manually copy the list.

## Config Options

| Option                        | Description                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------------- |
| Transform                     | Will apply specified movement, rotation, and scaling options to each imported scene.               |
| Merge Triangles               | Resulting import mesh will have a lot of doubles unless this option is enabled.                    |
| Merge Distance                | Distance to merge by. Modify this for tris very close to each other and not importing correctly.   |
| Modify Color Management       | Blender defaults to using Filmic colors. This option changes the scene to use sRGB colors for you. |
| Enable Material Transparency  | Makes triangles correctly display textures with alpha channels.                                    |
| Display Backface Culling      | Renders face sides based on their normal vector.                                                   |
| Enable Fog                    | Enables importing of fog information.                                                              |
| Blacklist                     | Whitelist when unchecked. Removes or only allows specified textures.                               |
| Textures                      | Specifies the texture filter list. Appropriate input is `(texture name, no extension),...`         |
