"""One-off cloth settle: let the flat-lay garment hang from its hanger.

The photo-derived mesh keeps the flat-lay pose (sleeves spread, hem stiff). A
low-res closed proxy of the same silhouette is pinned along the shoulder line
and simulated under gravity on a static hanger; the render mesh follows it
through a Surface Deform modifier. The final pose is saved and becomes the rest
shape of every garment, while the motion stays on the spring armature, so holds
remain perfectly still.

Ported from the cloth exploration in the lab (full per-frame cloth was judged
not worth it: uncontrollable hood, holds that never come to rest).
"""

import math
import time

import bmesh
import bpy
import numpy as np

from . import config as rail_config
from . import hood as rail_hood
from . import scene as rail_scene

DRAPE_FILE = rail_config.TEXTURES / "drape.npz"


def proxy_mesh(cfg, photo, sub):
    """Closed low-res shell from the silhouette: front and back share the seam vertices.

    Built from grid cells whose four corners are inside the silhouette (snapped
    contour points pile up and break the manifold), so the shell is inset by up
    to one cell. One-cell-wide features are pruned, since their front and back
    would be the same faces.
    """
    inside = photo["inside"][::sub, ::sub]
    heights = photo["heights"][::sub, ::sub]
    m = float(photo["m_per_px"])
    step = int(photo["step_px"]) * sub
    fx0, fx1, fy0, _ = photo["front_bbox"]

    cell = inside[:-1, :-1] & inside[1:, :-1] & inside[:-1, 1:] & inside[1:, 1:]
    while True:
        count = np.zeros(inside.shape, dtype=np.int8)  # kept cells around each grid point
        count[:-1, :-1] += cell
        count[1:, :-1] += cell
        count[:-1, 1:] += cell
        count[1:, 1:] += cell
        seam = (count > 0) & (count < 4)
        flat = seam[:-1, :-1] & seam[1:, :-1] & seam[:-1, 1:] & seam[1:, 1:] & cell
        if not flat.any():
            break
        cell &= ~flat
    used = count > 0
    interior = count == 4

    front_idx = np.full(inside.shape, -1, dtype=np.int64)
    n = int(used.sum())
    front_idx[used] = np.arange(n)
    back_idx = front_idx.copy()
    back_idx[interior] = n + np.arange(int(interior.sum()))

    rows, cols = np.nonzero(used)
    rin, cin = np.nonzero(interior)
    all_r, all_c = np.concatenate([rows, rin]), np.concatenate([cols, cin])
    x = (all_c * step - (fx0 + fx1) / 2) * m
    z = -(all_r * step - fy0) * m - cfg["garment"]["hook_clearance_m"]
    # The render mesh has a round (vertical) rim; the proxy only drives it and needs
    # gentler quads to bind, so its heights go back to a sine ramp.
    thickness = cfg["garment"]["thickness_m"]
    t = 1.0 - np.sqrt(1.0 - np.clip(heights[all_r, all_c] / thickness, 0.0, 1.0) ** 2)
    h = thickness * np.sin(np.pi / 2 * t) * interior[all_r, all_c]
    side = np.r_[-np.ones(n), np.ones(len(rin))]
    verts = np.stack([x, side * h, z], 1)

    cr, cc = np.nonzero(cell)
    front_faces = np.stack([front_idx[cr, cc], front_idx[cr + 1, cc], front_idx[cr + 1, cc + 1], front_idx[cr, cc + 1]], 1)
    # Back loops start one corner later, so triangulation splits front and back along
    # opposite diagonals (a diagonal joining two seam vertices is never shared).
    back_faces = np.stack([back_idx[cr, cc + 1], back_idx[cr + 1, cc + 1], back_idx[cr + 1, cc], back_idx[cr, cc]], 1)

    mesh = bpy.data.meshes.new("drape_proxy")
    mesh.from_pydata(verts.tolist(), [], np.concatenate([front_faces, back_faces]).tolist())
    bm = bmesh.new()
    bm.from_mesh(mesh)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    print(f"drape proxy: {len(mesh.vertices)} verts, {non_manifold} non-manifold edges, cell {step * m * 1000:.1f} mm")
    return mesh


def pin_weights(co, drape, z_hinge=None):
    """Weight 1 on a band under the shoulder line, where the hanger holds the garment,
    and on the hood, which is folded afterwards rather than simulated."""
    line = drape["hanger_line"]
    ax, z = np.abs(co[:, 0]), co[:, 2]
    shoulder = rail_scene.shoulder_line(ax, line)
    band = (ax <= line["half_width_m"]) & (z <= shoulder + 0.012) & (z >= shoulder - line["band_m"])
    if z_hinge is not None:
        band |= z > z_hinge - 0.005
    return band.astype(np.float32)


def add_cloth(proxy, drape, frames):
    mod = proxy.modifiers.new("Cloth", "CLOTH")
    s = mod.settings
    s.quality = drape["quality"]
    s.mass = drape["mass"]
    s.air_damping = drape["air_damping"]
    s.tension_stiffness = s.compression_stiffness = drape["tension"]
    s.shear_stiffness = drape["shear"]
    s.bending_stiffness = drape["bending"]
    s.tension_damping = s.compression_damping = s.shear_damping = drape["damping"]
    s.bending_damping = 0.5
    s.vertex_group_mass = "pin"
    s.pin_stiffness = 1.0
    mod.collision_settings.use_collision = False
    mod.collision_settings.use_self_collision = False
    mod.point_cache.frame_start = 1
    mod.point_cache.frame_end = frames
    return mod


def add_wind(scene, drape):
    """A puff toward the garment's back during the first frames, to tip the hood over."""
    bpy.ops.object.effector_add(type="WIND")
    wind = bpy.context.active_object
    wind.location = (0.0, -1.0, -0.2)
    wind.rotation_euler = (-math.pi / 2, 0.0, 0.0)  # local +Z blows toward +Y, the back
    wind.field.flow = 0.0
    stop = drape["wind_frames"]
    for frame, strength in ((1, drape["wind"]), (stop, drape["wind"]), (stop + 10, 0.0)):
        wind.field.strength = strength
        wind.field.keyframe_insert("strength", frame=frame)


def run(config_path=None):
    cfg = rail_config.load(config_path)
    drape = cfg["drape"]
    photo = dict(np.load(rail_config.TEXTURES / "garment.npz"))
    frames = drape["frames"]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, frames
    collection = scene.collection

    hoodie = rail_scene._link(bpy.data.objects.new("hoodie", rail_scene.garment_mesh(cfg, photo)), collection)
    proxy = rail_scene._link(bpy.data.objects.new("proxy", proxy_mesh(cfg, photo, drape["proxy_sub"])), collection)
    z_hinge = None
    if cfg["hood"]["enabled"]:
        # Same neck thinning as the scene, so the drape starts from the final shape.
        z_hinge = rail_hood.hinge_z(cfg, photo)
        # The hood is pinned here and folded afterwards, so it keeps no bag: its
        # volume would only make proxy polygons the Surface Deform bind rejects.
        for obj in (hoodie, proxy):
            co = rail_scene._vertex_coords(obj.data)
            obj.data.vertices.foreach_set("co", rail_hood.taper(co, z_hinge, cfg["hood"], volume=False).ravel())
            obj.data.update()
    weights = pin_weights(rail_scene._vertex_coords(proxy.data), drape, z_hinge)
    proxy.vertex_groups.new(name="pin").add(np.nonzero(weights > 0)[0].tolist(), 1.0, "REPLACE")
    print(f"drape pins: {int(weights.sum())} vertices")
    cloth = add_cloth(proxy, drape, frames)

    # Bind once, on the flat proxy: binding a crumpled pose fails on degenerate polygons.
    deform = hoodie.modifiers.new("SurfaceDeform", "SURFACE_DEFORM")
    deform.target = proxy
    scene.frame_set(1)
    with bpy.context.temp_override(object=hoodie, active_object=hoodie, selected_objects=[hoodie]):
        bpy.ops.object.surfacedeform_bind(modifier=deform.name)
    bpy.context.evaluated_depsgraph_get().update()
    if not deform.is_bound:
        raise SystemExit("Surface Deform failed to bind")
    if drape["wind"] > 0:
        add_wind(scene, drape)

    started = time.time()
    with bpy.context.temp_override(scene=scene, point_cache=cloth.point_cache, active_object=proxy, object=proxy):
        bpy.ops.ptcache.bake(bake=True)
    print(f"drape bake: {frames} frames in {time.time() - started:.1f} s")

    scene.frame_set(frames)
    evaluated = hoodie.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    draped = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", draped)
    evaluated.to_mesh_clear()
    draped = draped.reshape(-1, 3)
    np.savez_compressed(DRAPE_FILE, co=draped)
    print(f"drape: wrote {DRAPE_FILE} ({len(draped)} vertices, "
          f"width {np.ptp(draped[:, 0]):.2f} m, height {np.ptp(draped[:, 2]):.2f} m, depth {np.ptp(draped[:, 1]):.2f} m)")
