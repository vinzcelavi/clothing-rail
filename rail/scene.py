"""Blender scene construction: garments, rail, wall, lights, camera and baked animation.

Every garment is the same photo-derived mesh (shared data); color, heather and
logo come from per-object materials. Hierarchy per slot:

    slot_<i>    empty on the rail: slides along X, sways around Y
      hook_<i>    chrome hook, stays on the rail
      swivel_<i>  empty: turns around Z toward the camera
        hanger_<i>  wooden hanger, showing between the hood's sides
        rig_<i>     armature: chest, hem and sleeves lag and settle like fabric
        hoodie_<i>  garment mesh (draped, hood folded back), deformed by rig_<i>

Animation is baked as one linear key per frame from rail.motion.
"""

import math

import bpy
import numpy as np
from mathutils import Vector

from . import config as rail_config
from . import hood as rail_hood
from . import sleeves as rail_sleeves
from . import variation as rail_variation
from . import wrinkles as rail_wrinkles
from .motion import BONES, Choreography


def _node(tree, kind, **props):
    node = tree.nodes.new(kind)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _math(tree, op, a, b, c=None):
    node = _node(tree, "ShaderNodeMath", operation=op)
    for i, value in enumerate((a, b, c)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            node.inputs[i].default_value = value
        else:
            tree.links.new(value, node.inputs[i])
    return node.outputs[0]


def _scale_color(tree, rgba, factor):
    node = _node(tree, "ShaderNodeVectorMath", operation="SCALE")
    node.inputs[0].default_value = rgba[:3]
    tree.links.new(factor, node.inputs["Scale"])
    return node.outputs[0]


def _image(tree, image, vector, extension="REPEAT"):
    node = _node(tree, "ShaderNodeTexImage", image=image, extension=extension)
    tree.links.new(vector, node.inputs["Vector"])
    return node


def _new_material(name):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat, mat.node_tree


def _principled(tree, **inputs):
    bsdf = _node(tree, "ShaderNodeBsdfPrincipled")
    for key, value in inputs.items():
        bsdf.inputs[key].default_value = value
    out = _node(tree, "ShaderNodeOutputMaterial")
    tree.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return bsdf


def solid_material(name, hex_color, **inputs):
    mat, tree = _new_material(name)
    _principled(tree, **{"Base Color": rail_config.hex_to_linear(hex_color), **inputs})
    return mat


def load_images():
    images = {}
    for key in ("front_detail", "back_detail", "logo"):
        img = bpy.data.images.load(str(rail_config.TEXTURES / f"{key}.png"), check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        images[key] = img
    return images


def garment_material(name, slot, side, cfg, images, photo):
    """Recolor the photo: target color x fabric shading x heather speckle, plus the logo."""
    g = cfg["garment"]
    mat, tree = _new_material(name)
    bsdf = _principled(tree, **{"Roughness": g["roughness"], "Sheen Weight": g["sheen"], "Sheen Roughness": 0.5})

    uv_front = _node(tree, "ShaderNodeUVMap", uv_map="front").outputs["UV"]
    uv_side = uv_front if side == "front" else _node(tree, "ShaderNodeUVMap", uv_map="back").outputs["UV"]

    detail = _image(tree, images[f"{side}_detail"], uv_side).outputs["Color"]
    if side == "front" and cfg["hood"]["enabled"]:
        # The folded hood shows its outer shell, center seam included: on the flat
        # lay only the back photo shows it (the front one shows the lining).
        uv_back = _node(tree, "ShaderNodeUVMap", uv_map="back").outputs["UV"]
        shell = _node(tree, "ShaderNodeMix", data_type="RGBA")
        tree.links.new(_node(tree, "ShaderNodeAttribute", attribute_name="hood_shell").outputs["Fac"], shell.inputs[0])
        tree.links.new(detail, shell.inputs[6])
        tree.links.new(_image(tree, images["back_detail"], uv_back).outputs["Color"], shell.inputs[7])
        detail = shell.outputs[2]
    shade = _math(tree, "MULTIPLY", detail, 2.0)

    noise = _node(tree, "ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = g["heather_scale"]
    noise.inputs["Detail"].default_value = 1.0
    tree.links.new(uv_front, noise.inputs["Vector"])
    k = 0.7 * slot.get("heather", 0.0)
    heather = _math(tree, "MULTIPLY_ADD", noise.outputs["Fac"], k, 1.0 - 0.5 * k)
    color = _scale_color(tree, rail_config.hex_to_linear(slot["color"]), _math(tree, "MULTIPLY", shade, heather))

    bump = _node(tree, "ShaderNodeBump")
    bump.inputs["Strength"].default_value = g["bump"]
    bump.inputs["Distance"].default_value = 0.003
    tree.links.new(shade, bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    if side == "front" and slot.get("logo"):
        logo = g["logo"]
        w_px, h_px = photo["image_size"]
        lw, lh = images["logo"].size
        width_u = logo["width_px"] / w_px
        height_v = logo["width_px"] * lh / lw / h_px
        center_u = logo["center_px"][0] / w_px
        center_v = 1.0 - logo["center_px"][1] / h_px
        mapping = _node(tree, "ShaderNodeMapping", vector_type="POINT")
        mapping.inputs["Location"].default_value = (0.5 - center_u / width_u, 0.5 - center_v / height_v, 0.0)
        mapping.inputs["Scale"].default_value = (1.0 / width_u, 1.0 / height_v, 1.0)
        tree.links.new(uv_front, mapping.inputs["Vector"])
        logo_tex = _image(tree, images["logo"], mapping.outputs["Vector"], extension="CLIP")
        logo_color = _scale_color(tree, rail_config.hex_to_linear(slot["logo"]), shade)
        mix = _node(tree, "ShaderNodeMix", data_type="RGBA")
        tree.links.new(logo_tex.outputs["Alpha"], mix.inputs[0])
        tree.links.new(color, mix.inputs[6])
        tree.links.new(logo_color, mix.inputs[7])
        color = mix.outputs[2]

    tree.links.new(color, bsdf.inputs["Base Color"])
    return mat


def smooth_rim(verts, faces, weight, passes, factors=(0.5, -0.53)):
    """Taubin smoothing (shrink-free) of the vertices near the contour.

    Contour vertices are snapped to irregular pixel positions; relaxing them
    and their first rings turns the welded seam into an even, round rim.
    `factors=(0.5,)` gives plain Laplacian smoothing, which also rounds off.
    """
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 3]], faces[:, [3, 0]]])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    a, b = edges[:, 0], edges[:, 1]
    n = len(verts)
    degree = np.maximum(np.bincount(edges.ravel(), minlength=n), 1)
    for _ in range(passes):
        for factor in factors:
            neighbour_sum = np.stack(
                [np.bincount(a, verts[b, k], n) + np.bincount(b, verts[a, k], n) for k in range(3)], 1
            )
            verts = verts + factor * weight[:, None] * (neighbour_sum / degree[:, None] - verts)
    return verts


def garment_mesh(cfg, photo):
    """Inflated two-sided mesh from the photo silhouette, origin on the rail."""
    inside, snap = photo["inside"], photo["snap_px"]
    w_px, h_px = photo["image_size"]
    m = float(photo["m_per_px"])
    fx0, fx1, fy0, fy1 = photo["front_bbox"]
    bx0, bx1, by0, by1 = photo["back_bbox"]

    # A cell is kept if any corner is inside; its outside corners sit on the contour.
    cell = inside[:-1, :-1] | inside[1:, :-1] | inside[:-1, 1:] | inside[1:, 1:]
    used = np.zeros_like(inside)
    used[:-1, :-1] |= cell
    used[1:, :-1] |= cell
    used[:-1, 1:] |= cell
    used[1:, 1:] |= cell
    index = np.full(inside.shape, -1, dtype=np.int64)
    n = int(used.sum())
    index[used] = np.arange(n)

    rows, cols = np.nonzero(used)
    px_x, px_y = snap[rows, cols, 0], snap[rows, cols, 1]
    center_x = (fx0 + fx1) / 2
    x = (px_x - center_x) * m
    z = -(px_y - fy0) * m - cfg["garment"]["hook_clearance_m"]
    # Contour vertices have zero height and are shared by both halves: the seam
    # is welded, so smooth shading flows around the edge instead of creasing.
    on_contour = ~inside[rows, cols]
    h = photo["heights"][rows, cols] * ~on_contour
    interior = np.nonzero(~on_contour)[0]
    back_of = np.arange(n)
    back_of[interior] = n + np.arange(len(interior))
    verts = np.concatenate([np.stack([x, -h, z], 1), np.stack([x, h, z], 1)[interior]])

    cr, cc = np.nonzero(cell)
    a, b = index[cr, cc], index[cr, cc + 1]
    c, d = index[cr + 1, cc + 1], index[cr + 1, cc]
    front_faces = np.stack([a, d, c, b], 1)  # normal toward -Y, the camera
    back_faces = back_of[np.stack([a, b, c, d], 1)]
    faces = np.concatenate([front_faces, back_faces])
    # Smooth within the outer part of the round edge, fading out further in.
    rim = 1.0 - np.clip(np.abs(verts[:, 1]) / (0.6 * cfg["garment"]["thickness_m"]), 0.0, 1.0)
    verts = smooth_rim(verts, faces, rim, cfg["garment"]["rim_smoothing"])

    mesh = bpy.data.meshes.new("hoodie")
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.polygons.foreach_set(
        "material_index", np.r_[np.zeros(len(front_faces)), np.ones(len(back_faces))].astype(np.int32)
    )

    # "front" maps the front photo; "back" mirrors the back photo, hems aligned.
    v_front = 1.0 - px_y / h_px
    uv_front = np.stack([px_x / w_px, v_front], 1)
    back_x = (bx0 + bx1) / 2 - (px_x - center_x)
    uv_back = np.stack([back_x / w_px, 1.0 - (px_y + (by1 - fy1)) / h_px], 1)
    loop_verts = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    for name, uv in (("front", uv_front), ("back", uv_back)):
        layer = mesh.uv_layers.new(name=name)
        layer.data.foreach_set("uv", np.concatenate([uv, uv[interior]])[loop_verts].astype(np.float32).ravel())
    if cfg["hood"]["enabled"]:
        # Where the front material switches to the back photo on the hood (past the crease).
        hood = cfg["hood"]
        u = (verts[:, 2] - fold_line(verts[:, 0], cfg, photo)) * hood["length_scale"]
        shell = _ramp(u, hood["shell_from_m"], hood["shell_len_m"]).astype(np.float32)
        mesh.attributes.new("hood_shell", "FLOAT", "POINT").data.foreach_set("value", shell)

    mesh.shade_smooth()
    mesh.materials.append(None)
    mesh.materials.append(None)
    return mesh


def hook_curve(name, radius, stem_bottom_z):
    """Wire hook: a stem rising from the hanger crown, curling over the rail from front to back."""
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.0028
    curve.bevel_resolution = 4
    curve.use_fill_caps = True
    r = radius + 0.0045
    points = [(0.0, stem_bottom_z), (0.0, -0.045), (-0.004, -0.028), (-0.011, -0.016)]
    points += [(r * math.cos(math.radians(a)), r * math.sin(math.radians(a))) for a in range(215, -31, -10)]
    spline = curve.splines.new("NURBS")
    spline.points.add(len(points) - 1)
    for point, (y, z) in zip(spline.points, points):
        point.co = (0.0, y, z, 1.0)
    spline.order_u = 3
    spline.use_endpoint_u = True
    spline.resolution_u = 6
    return curve


def shoulder_line(ax, line):
    """Garment-space height of the shoulder line at |x| = ax, where the hanger holds the garment."""
    return np.where(
        ax < line["neck_half_width_m"],
        line["neck_z_m"],
        line["shoulder_z_m"] - line["slope"] * (ax - line["neck_half_width_m"]),
    )


def fold_line(x, cfg, photo, hood=None):
    """Where the hood turns back, per vertex (rail.hood.fold_line on this hanger and shoulders)."""
    return rail_hood.fold_line(
        x,
        rail_hood.hinge_z(cfg, photo),
        hood or cfg["hood"],
        lambda ax: hanger_top(ax, cfg),
        lambda ax: shoulder_line(ax, cfg["drape"]["hanger_line"]),
    )


def hanger_top(ax, cfg):
    """Garment-space height of the hanger's upper edge at |x| = ax.

    Arms sloping down from a rounded crown under the hook (`apex_z_m`, `slope`,
    `crown_radius_m`), curving down toward the tips (`curve`, per meter) like
    a contoured hanger, so they slip under the hood's sides and the
    shoulders; from `cap_from_m` on (over `cap_len_m`) they also stay `drop_m`
    under the shoulder line.
    """
    hanger = cfg["hanger"]
    r = hanger["crown_radius_m"]
    arm = hanger["apex_z_m"] - hanger["slope"] * (np.sqrt(ax * ax + r * r) - r) - hanger["curve"] * ax * ax
    cap = shoulder_line(ax, cfg["drape"]["hanger_line"]) - hanger["drop_m"]
    return arm - _ramp(ax, hanger["cap_from_m"], hanger["cap_len_m"]) * np.maximum(arm - cap, 0.0)


def hanger_mesh(cfg):
    """Flat wooden hanger: a rounded board swept along `hanger_top`, taller at the crown.

    The "grain" UV runs along the hanger (meters of arc length) and across its
    height, so the wood grain follows the arms.
    """
    hanger = cfg["hanger"]
    xs = np.linspace(-hanger["half_width_m"], hanger["half_width_m"], 161)
    half_heights = (hanger["height_m"] + (hanger["tip_height_m"] - hanger["height_m"]) * np.abs(xs) / xs[-1]) / 2
    tops = hanger_top(np.abs(xs), cfg)

    # Rounded-rectangle section (superellipse): Y through the hanger, N across its height.
    t = np.linspace(0.0, 2 * np.pi, 32, endpoint=False)
    sec_y = hanger["thickness_m"] / 2 * np.sign(np.cos(t)) * np.abs(np.cos(t)) ** 0.4
    sec_n = np.sign(np.sin(t)) * np.abs(np.sin(t)) ** 0.4
    tangent = np.stack([np.gradient(xs), np.gradient(tops)], 1)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], 1)  # in the XZ plane, pointing up
    # Sections hang below the top edge, so the upper edge is exactly `hanger_top`
    # even where the arms bend down under the shoulders.
    xc, zc = xs - half_heights * normal[:, 0], tops - half_heights * normal[:, 1]
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xc), np.diff(zc)))])

    ring = len(t)
    verts, uvs = [], []
    for x, z, (nx, nz), h, s in zip(xc, zc, normal, half_heights, arc):
        for y, n in zip(sec_y, sec_n):
            verts.append((x + n * h * nx, y, z + n * h * nz))
            uvs.append((s, n * h + y))
    faces = []
    for i in range(len(xs) - 1):
        for j in range(ring):
            a, b = i * ring + j, i * ring + (j + 1) % ring
            faces.append((a, a + ring, b + ring, b))
    faces.append(tuple(range(ring))[::-1])
    faces.append(tuple(range((len(xs) - 1) * ring, len(xs) * ring)))
    mesh = bpy.data.meshes.new("hanger")
    mesh.from_pydata(verts, [], faces)
    loop_verts = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    mesh.uv_layers.new(name="grain").data.foreach_set("uv", np.asarray(uvs, dtype=np.float32)[loop_verts].ravel())
    mesh.shade_smooth()
    return mesh, float(tops[len(xs) // 2])


def wood_material(hex_color):
    """Lacquered beech: fine grain lines along the hanger, slow tone variation, satin coat."""
    mat, tree = _new_material("wood")
    bsdf = _principled(tree, Roughness=0.38, **{"Coat Weight": 0.25, "Coat Roughness": 0.25})
    uv = _node(tree, "ShaderNodeUVMap", uv_map="grain").outputs["UV"]
    # Stretch along the arm: grain lines run lengthwise, a few millimeters apart.
    stretch = _node(tree, "ShaderNodeMapping", vector_type="POINT")
    stretch.inputs["Scale"].default_value = (6.0, 120.0, 1.0)
    tree.links.new(uv, stretch.inputs["Vector"])
    grain = _node(tree, "ShaderNodeTexWave", wave_type="BANDS", bands_direction="Y", wave_profile="SAW")
    grain.inputs["Scale"].default_value = 1.0
    grain.inputs["Distortion"].default_value = 4.0
    grain.inputs["Detail"].default_value = 2.0
    grain.inputs["Detail Scale"].default_value = 0.6
    tree.links.new(stretch.outputs["Vector"], grain.inputs["Vector"])
    tone = _node(tree, "ShaderNodeTexNoise")
    tone.inputs["Scale"].default_value = 25.0
    tone.inputs["Detail"].default_value = 2.0
    tree.links.new(uv, tone.inputs["Vector"])
    shade = _math(tree, "MULTIPLY_ADD", grain.outputs["Fac"], -0.1, 1.0)
    shade = _math(tree, "MULTIPLY", shade, _math(tree, "MULTIPLY_ADD", tone.outputs["Fac"], 0.16, 0.92))
    tree.links.new(_scale_color(tree, rail_config.hex_to_linear(hex_color), shade), bsdf.inputs["Base Color"])
    return mat


def _link(obj, collection):
    collection.objects.link(obj)
    return obj


def _empty(name, collection, parent=None):
    obj = _link(bpy.data.objects.new(name, None), collection)
    obj.parent = parent
    obj.empty_display_size = 0.05
    return obj


def _area_light(name, collection, position, size, energy, target):
    data = bpy.data.lights.new(name, "AREA")
    data.shape = "SQUARE"
    data.size = size
    data.energy = energy
    obj = _link(bpy.data.objects.new(name, data), collection)
    obj.location = position
    obj.rotation_euler = (Vector(target) - Vector(position)).to_track_quat("-Z", "Y").to_euler()
    return obj


def wall_material(hex_color):
    """Matte plaster: faint large-scale tone variation and a fine grain."""
    mat, tree = _new_material("wall")
    bsdf = _principled(tree, Roughness=0.95)
    coords = _node(tree, "ShaderNodeTexCoord").outputs["Object"]
    blotch = _node(tree, "ShaderNodeTexNoise")
    blotch.inputs["Scale"].default_value = 1.5
    blotch.inputs["Detail"].default_value = 3.0
    tree.links.new(coords, blotch.inputs["Vector"])
    tone = _math(tree, "MULTIPLY_ADD", blotch.outputs["Fac"], 0.08, 0.96)
    tree.links.new(_scale_color(tree, rail_config.hex_to_linear(hex_color), tone), bsdf.inputs["Base Color"])
    grain = _node(tree, "ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 400.0
    tree.links.new(coords, grain.inputs["Vector"])
    bump = _node(tree, "ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    tree.links.new(grain.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _ramp(values, start, width):
    t = np.clip((values - start) / width, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def rig_weights(hoodie, rig, flat):
    """Skin a garment mesh: hood and shoulders stay on the hanger,
    the body below bends at the shoulder line then at the waist, sleeves
    swing from the shoulder seams. Regions are read on the flat-lay pose,
    where sleeves and body are still apart."""
    x, z = flat[:, 0], flat[:, 2]
    blend = rig["blend_m"]
    sleeve = rail_sleeves.membership(flat, rig)
    lower = _ramp(-z, -rig["shoulder_z"], 2 * blend)
    hem = _ramp(-z, -rig["waist_z"], 3 * blend)
    weights = {
        "root": (1 - lower) * (1 - sleeve),
        "chest": lower * (1 - hem) * (1 - sleeve),
        "hem": lower * hem * (1 - sleeve),
        "sleeve_pos": sleeve * (x > 0),
        "sleeve_neg": sleeve * (x <= 0),
    }
    # Groups live on the mesh: one call per garment mesh.
    for name, w in weights.items():
        group = hoodie.vertex_groups.new(name=name)
        levels = np.round(w * 100).astype(int)
        for level in np.unique(levels):
            if level > 0:
                group.add(np.nonzero(levels == level)[0].tolist(), level / 100.0, "REPLACE")


def _vertex_coords(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def load_drape(cfg, vertex_count):
    """Hanging pose from scripts/drape.py, or None to keep the flat-lay pose."""
    path = rail_config.TEXTURES / "drape.npz"
    if not cfg["drape"]["enabled"] or not path.exists():
        return None
    co = np.load(path)["co"]
    if len(co) != vertex_count:
        raise SystemExit(f"{path} does not match the garment mesh; rerun the drape step")
    return co


def rig_joints(rig, flat, posed):
    """Bone joints placed on the flat-lay silhouette, moved to where the drape took them."""

    def follow(x, z):
        point = rail_sleeves.follow(flat, posed, x, z)
        return (float(point[0]), float(point[2]))

    (root_x, root_z), (tip_x, tip_z) = rig["sleeve_root"], rig["sleeve_tip"]
    return {
        "shoulder": follow(0.0, rig["shoulder_z"]),
        "waist": follow(0.0, rig["waist_z"]),
        "hem": follow(0.0, rig["hem_z"]),
        "sleeve_pos": (follow(root_x, root_z), follow(tip_x, tip_z)),
        "sleeve_neg": (follow(-root_x, root_z), follow(-tip_x, tip_z)),
    }


def rig_armature(joints, collection):
    """Shared armature data; returns it with a first object that uses it."""
    data = bpy.data.armatures.new("fabric")
    obj = _link(bpy.data.objects.new("rig_0", data), collection)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name, head, tail, parent=None):
        b = data.edit_bones.new(name)
        b.head = (head[0], 0.0, head[1])
        b.tail = (tail[0], 0.0, tail[1])
        b.align_roll((0.0, 1.0, 0.0))  # local X across the garment, local Z through it
        b.parent = parent
        return b

    root = bone("root", (0.0, -0.02), joints["shoulder"])
    chest = bone("chest", joints["shoulder"], joints["waist"], root)
    bone("hem", joints["waist"], joints["hem"], chest)
    for name in ("sleeve_pos", "sleeve_neg"):
        head, tail = joints[name]
        bone(name, head, tail, root)
    bpy.ops.object.mode_set(mode="OBJECT")
    return data, obj


def _bake_channel(obj, data_path, index, values):
    """One linear key per frame, written in bulk."""
    if obj.animation_data is None:
        obj.animation_data_create()
    if obj.animation_data.action is None:
        obj.animation_data.action = bpy.data.actions.new(f"{obj.name}_action")
    fcurve = obj.animation_data.action.fcurve_ensure_for_datablock(obj, data_path, index=index)
    count = len(values)
    co = np.empty(2 * count)
    co[0::2] = np.arange(1, count + 1)
    co[1::2] = values
    fcurve.keyframe_points.add(count)
    fcurve.keyframe_points.foreach_set("co", co)
    fcurve.keyframe_points.foreach_set("interpolation", [1] * count)  # LINEAR
    fcurve.update()


def setup_render(scene, cfg, frame_count):
    out, rnd = cfg["output"], cfg["render"]
    scene.render.engine = "CYCLES"
    scene.cycles.samples = rnd["samples"]
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 6
    scene.cycles.transparent_max_bounces = 8
    scene.render.resolution_x = out["width"]
    scene.render.resolution_y = out["height"]
    scene.render.resolution_percentage = 100
    scene.render.fps = out["fps"]
    scene.frame_start = 1
    scene.frame_end = frame_count
    scene.render.film_transparent = out["transparent"]
    scene.render.use_motion_blur = rnd["motion_blur"]
    scene.render.motion_blur_shutter = rnd["shutter"]
    scene.render.use_persistent_data = True
    image = scene.render.image_settings
    image.file_format = "PNG"
    image.color_mode = "RGBA"
    image.color_depth = "8"
    image.compression = 15
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = rnd["exposure"]


def setup_world(scene, cfg):
    world = bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = rail_config.hex_to_linear(cfg["lights"]["world_color"])
    background.inputs["Strength"].default_value = cfg["lights"]["world_strength"]


def build_stage(scene, cfg, collection):
    stage, cam, lights = cfg["stage"], cfg["camera"], cfg["lights"]
    rail_z = stage["rail_z_m"]

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48, radius=stage["rail_radius_m"], depth=12.0, location=(0, 0, rail_z), rotation=(0, math.pi / 2, 0)
    )
    rail = bpy.context.active_object
    rail.name = "rail"
    rail.data.shade_smooth()
    rail.data.materials.append(solid_material("rail", stage["rail_color"], Metallic=1.0, Roughness=0.35))

    bpy.ops.mesh.primitive_plane_add(size=12.0, location=(0, stage["wall_y_m"], rail_z - 1.0), rotation=(math.pi / 2, 0, 0))
    wall = bpy.context.active_object
    wall.name = "wall"
    wall.data.materials.append(wall_material(stage["wall_color"]))
    # Transparent renders keep only the wall's shadows, for compositing over any background.
    wall.is_shadow_catcher = cfg["output"]["transparent"]

    rail_origin = Vector((0.0, 0.0, rail_z))
    for name, light in lights["areas"].items():
        target = Vector(light.get("target", (0.0, 0.0, -0.4))) + rail_origin
        _area_light(name, collection, Vector(light["position"]) + rail_origin, light["size_m"], light["energy_w"], target)

    data = bpy.data.cameras.new("camera")
    data.lens = cam["lens_mm"]
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = 36.0
    distance = cam["visible_width_m"] * cam["lens_mm"] / 36.0
    frame_height = cam["visible_width_m"] * cfg["output"]["height"] / cfg["output"]["width"]
    camera = _link(bpy.data.objects.new("camera", data), collection)
    camera.location = (0.0, -distance, rail_z - (0.5 - cam["rail_from_top"]) * frame_height)
    camera.rotation_euler = (math.pi / 2, 0.0, 0.0)
    scene.camera = camera


def _posed_shape(cfg, photo, flat, faces, pair, draped, shape):
    """Final rest shape of one garment, and the coordinates its skin weights read.

    `shape` comes from rail.variation: hood overrides, sleeve pose, fold seed.
    """
    posed, weight_source = (flat if draped is None else draped), flat
    z_hinge = rail_hood.hinge_z(cfg, photo)
    if cfg["hood"]["enabled"]:
        hood = {**cfg["hood"], **shape["hood"]}
        inflation = (cfg["garment"]["thickness_m"], cfg["garment"]["round_m"])
        # The hood is shaped as if its flare over the shoulders were already tucked in.
        shoulder = lambda ax: shoulder_line(ax, cfg["drape"]["hanger_line"])  # noqa: E731
        tucked = rail_hood.tuck(flat, flat, hood, shoulder)
        z_fold = fold_line(tucked[:, 0], cfg, photo, hood)
        tapered = rail_hood.taper(tucked, z_hinge, hood, inflation=inflation, z_fold=z_fold)
        shaped = rail_hood.recess(tapered, tapered, z_hinge, hood, z_fold)
        posed = rail_hood.tuck(tapered if draped is None else draped, flat, hood, shoulder)
        posed = rail_hood.recess(posed, tapered, z_hinge, hood, z_fold)
        posed = rail_hood.fold(posed, shaped, z_hinge, z_fold, hood)
        # Up the hood's front edges the fold line rises fast and the lining
        # switches from pushed back to turned over: neighbouring mesh columns
        # turn over centimeters apart. Relax the stepped crest, or it tears
        # open around the arm.
        st = hood["stand"]
        ax = np.abs(tucked[:, 0])
        edge = _ramp(ax, st["open_half_width_m"] - 0.006, 0.006) * (
            1.0 - _ramp(ax, st["open_half_width_m"] + st["ramp_m"], 0.008)
        )
        near = 1.0 - _ramp(np.abs(flat[:, 2] - z_fold - 0.02), 0.025, 0.015)
        posed = smooth_rim(posed, faces, edge * near, st["edge_passes"], factors=(0.5,))
        # The folded hood lies on the upper back: skin it like the back it rests on.
        weight_source = rail_hood.fold(shaped, shaped, z_hinge, z_fold, hood)
    posed = rail_sleeves.cuffs_and_fullness(posed, flat, pair, cfg, shape["fullness"])
    posed = rail_sleeves.bend(posed, flat, cfg["fabric"]["rig"], shape["sleeves"])
    if cfg["wrinkles"]["enabled"] and shape["wrinkle_seed"] is not None:
        m = float(photo["m_per_px"])
        fx0, fx1, fy0, _ = photo["front_bbox"]
        lx, ly = cfg["garment"]["logo"]["center_px"]
        logo = ((lx - (fx0 + fx1) / 2) * m, -(ly - fy0) * m - cfg["garment"]["hook_clearance_m"])
        posed = rail_wrinkles.wrinkles(
            posed, flat, faces, z_hinge, cfg, shape["wrinkle_seed"], logo, shape["sleeve_folds"]
        )
    return posed, weight_source


def build_garments(cfg, collection, photo):
    """One mesh per garment (own hood fall, sleeve pose and folds), one slot per garment."""
    images = load_images()
    base = garment_mesh(cfg, photo)
    flat = _vertex_coords(base)
    faces = np.empty(len(base.polygons) * 4, dtype=np.int64)
    base.polygons.foreach_get("vertices", faces)
    faces = faces.reshape(-1, 4)
    draped = load_drape(cfg, len(flat))
    pair = rail_sleeves.counterparts(flat)
    # Bones sit on the plain shape; each garment's variation is baked into its mesh.
    reference, _ = _posed_shape(cfg, photo, flat, faces, pair, draped, rail_variation.NEUTRAL)

    hook_mat = solid_material("hook", cfg["stage"]["hook_color"], Metallic=1.0, Roughness=0.2)
    wood = wood_material(cfg["hanger"]["color"])
    hanger_data, crown_top = hanger_mesh(cfg)
    hanger_data.materials.append(wood)
    lift = cfg["hanger"]["lift_m"]
    rail_z = cfg["stage"]["rail_z_m"]
    rig_data, first_rig = rig_armature(rig_joints(cfg["fabric"]["rig"], flat, reference), collection)
    slots, swivels, rigs = [], [], []
    for i, slot in enumerate(rail_config.slots(cfg)):
        slot_obj = _empty(f"slot_{i}", collection)
        slot_obj.location = (0.0, 0.0, rail_z)
        slot_obj["garment"] = slot["name"]

        hook_data = hook_curve(f"hook_{i}", cfg["stage"]["rail_radius_m"], crown_top + lift)
        hook = _link(bpy.data.objects.new(f"hook_{i}", hook_data), collection)
        hook.data.materials.append(hook_mat)
        hook.parent = slot_obj

        swivel = _empty(f"swivel_{i}", collection, parent=slot_obj)
        # Garment, rig and hanger hang `lift` higher than the photo layout put them.
        hanger = _link(bpy.data.objects.new(f"hanger_{i}", hanger_data), collection)
        hanger.parent = swivel
        hanger.location.z = lift
        rig = first_rig if i == 0 else _link(bpy.data.objects.new(f"rig_{i}", rig_data), collection)
        rig.parent = swivel
        rig.location.z = lift
        bpy.context.view_layer.update()  # a new armature object has no pose until then
        for pose_bone in rig.pose.bones:
            pose_bone.rotation_mode = "XYZ"
        mesh = base if i == 0 else base.copy()
        mesh.name = f"hoodie_{i}"
        posed, weight_source = _posed_shape(cfg, photo, flat, faces, pair, draped, rail_variation.slot_shape(cfg, i))
        mesh.vertices.foreach_set("co", posed.ravel())
        mesh.update()
        hoodie = _link(bpy.data.objects.new(f"hoodie_{i}", mesh), collection)
        hoodie.parent = swivel
        hoodie.location.z = lift
        rig_weights(hoodie, cfg["fabric"]["rig"], weight_source)
        hoodie.modifiers.new("fabric", "ARMATURE").object = rig
        for index, side in enumerate(("front", "back")):
            hoodie.material_slots[index].link = "OBJECT"
            hoodie.material_slots[index].material = garment_material(
                f"{slot['name']} {side}", slot, side, cfg, images, photo
            )
        slots.append(slot_obj)
        swivels.append(swivel)
        rigs.append(rig)
    return slots, swivels, rigs


def bake_animation(frames, slots, swivels, rigs):
    for k, (slot, swivel, rig) in enumerate(zip(slots, swivels, rigs)):
        _bake_channel(slot, "location", 0, [f.x[k] for f in frames])
        _bake_channel(slot, "rotation_euler", 1, [f.sway[k] for f in frames])
        _bake_channel(swivel, "rotation_euler", 2, [f.swivel[k] for f in frames])
        for name in BONES:
            for axis in range(3):
                values = [f.bones[name][k][axis] for f in frames]
                if any(values):
                    _bake_channel(rig, f'pose.bones["{name}"].rotation_euler', axis, values)


def build(config_path=None):
    cfg = rail_config.load(config_path)
    photo = dict(np.load(rail_config.TEXTURES / "garment.npz"))
    choreography = Choreography(cfg)
    fps = cfg["output"]["fps"]
    frames = choreography.bake(fps)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    collection = scene.collection
    setup_render(scene, cfg, len(frames))
    setup_world(scene, cfg)
    build_stage(scene, cfg, collection)
    slots, swivels, rigs = build_garments(cfg, collection, photo)
    bake_animation(frames, slots, swivels, rigs)

    scene["rail_hold_frames"] = choreography.hold_frames(fps)
    scene["rail_animated"] = [obj.name for obj in slots + swivels]
    scene["rail_rigs"] = [obj.name for obj in rigs]
    scene.frame_set(choreography.hold_frames(fps)[0])

    rail_config.BUILD.mkdir(exist_ok=True)
    path = rail_config.BUILD / "rail.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=True)
    print(f"saved {path} ({len(frames)} frames, holds at {list(scene['rail_hold_frames'])})")
