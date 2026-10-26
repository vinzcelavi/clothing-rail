BLENDER ?= blender
CONFIG ?= config/rail.json
RENDER = $(BLENDER) -b build/rail.blend -P scripts/render.py -- --config $(CONFIG)

TEXTURES = build/textures/garment.npz
DRAPE = build/textures/drape.npz

.PHONY: textures drape scene stills preview render clean

# Photos -> silhouette, fabric shading maps, mesh grid, logo
textures: $(TEXTURES)
$(TEXTURES): $(CONFIG) scripts/prepare_textures.py $(wildcard assets/photos/*.png) $(wildcard assets/logo.png)
	uv run scripts/prepare_textures.py $(CONFIG)

# Cloth sim, once: the flat-lay garment settles on its hanger (~20 s)
drape: $(DRAPE)
$(DRAPE): $(TEXTURES) scripts/drape.py rail/drape.py rail/hood.py rail/scene.py
	$(BLENDER) -b --factory-startup -P scripts/drape.py -- $(CONFIG)

# Config + textures + drape -> build/rail.blend (geometry, materials, baked animation)
scene: $(DRAPE)
	$(BLENDER) -b --factory-startup -P scripts/build_scene.py -- $(CONFIG)

# One quick frame per hold, to check colors and framing
stills: scene
	$(RENDER) --preview --stills

# Whole animation at half resolution + MP4 over the background plate
preview: scene
	$(RENDER) --preview --plate
	$(RENDER) --preview --mp4

# Final PNG sequence + background plate; rerun to resume after an interruption
render: scene
	$(RENDER) --plate
	$(RENDER) --resume --mp4

clean:
	rm -rf build renders
