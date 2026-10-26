"""Light drape folds, so the fabric is not a perfectly smooth shell.

A sum of ridged waves, mostly vertical (garments hanging from a hanger fold
along their length), shown in patches rather than everywhere, displaced along
the vertex normals of the posed mesh. Stronger on the sleeves, spared on the
neck, the hood and the logo. Front and back get their own folds, blended on
the rim so the seam stays continuous. Sleeves can also get a few broad,
soft diagonal folds (`sleeve_folds`, drawn per garment). Pure numpy.
"""

import numpy as np

from . import sleeves as rail_sleeves


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def vertex_normals(co, faces):
    a, b, c, d = (co[faces[:, i]] for i in range(4))
    face_normal = np.cross(c - a, d - b)
    n = len(co)
    normals = np.stack(
        [sum(np.bincount(faces[:, i], face_normal[:, k], n) for i in range(4)) for k in range(3)], 1
    )
    return normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)


def _ridges(x, z, rng, count, wavelengths):
    """Normalised sum of ridged waves running roughly vertically."""
    field = np.zeros_like(x)
    total = 0.0
    for _ in range(count):
        k = 2.0 * np.pi / rng.uniform(*wavelengths)
        angle = rng.normal(0.0, 0.3)  # wave vector near horizontal: folds near vertical
        weight = rng.uniform(0.5, 1.0)
        wave = np.sin(k * (np.cos(angle) * x + np.sin(angle) * z) + rng.uniform(0.0, 2.0 * np.pi))
        field += weight * np.sign(wave) * np.abs(wave) ** 0.6
        total += weight
    return field / total


def _sleeve_folds(flat, cfg, folds, side_weight):
    """A few broad, soft diagonal folds on the sleeves, one bump each."""
    rig = cfg["fabric"]["rig"]
    member = rail_sleeves.membership(flat, rig)
    (rx, rz), (tx, tz) = rig["sleeve_root"], rig["sleeve_tip"]
    field = np.zeros(len(flat))
    for fold in folds:
        sign = 1.0 if fold["side"] == "pos" else -1.0
        t, length = rail_sleeves.axis(flat, rig, sign)
        direction = np.array([sign * (tx - rx), tz - rz]) / length
        normal = np.array([-direction[1], direction[0]])
        across = (flat[:, 0] - sign * rx) * normal[0] + (flat[:, 2] - rz) * normal[1]
        angle = np.radians(fold["angle_deg"])
        ridge = t * length * np.cos(angle) + across * np.sin(angle) - fold["t"] * length * np.cos(angle)
        bump = np.exp(-((ridge / cfg["wrinkles"]["sleeve_fold_width_m"]) ** 2))
        face = {"front": side_weight, "back": 1.0 - side_weight, "both": np.ones(len(flat))}[fold["face"]]
        field += member * (sign * flat[:, 0] > 0) * bump * face * fold["strength"]
    return field


def wrinkles(posed, flat, faces, z_hinge, cfg, seed, logo_center, sleeve_folds=()):
    w = cfg["wrinkles"]
    rng = np.random.default_rng(seed)
    x, y, z = flat.T
    thickness = cfg["garment"]["thickness_m"]

    front = _ridges(x, z, rng, w["count"], w["wavelength_m"])
    back = _ridges(x, z, rng, w["count"], w["wavelength_m"])
    side = np.clip(0.5 - y / (1.2 * thickness), 0.0, 1.0)  # 1 front, 0 back, 0.5 on the rim
    field = side * front + (1.0 - side) * back

    patch = 2.0 * np.pi / w["patch_m"]
    a, b = rng.uniform(0.0, 2.0 * np.pi, 2)
    patches = _smoothstep((np.sin(patch * x + a) * np.sin(0.6 * patch * z + b) + 0.3) / 0.9)

    region = 1.0 - _smoothstep((z - (z_hinge - 0.08)) / 0.06)  # nothing on the neck and hood
    logo = np.hypot(x - logo_center[0], z - logo_center[1])
    region *= 1.0 - 0.8 * (1.0 - _smoothstep((logo - 0.05) / 0.04))
    sleeve = _smoothstep((np.abs(x) - 0.17) / 0.03)
    amplitude = w["amplitude_m"] * (1.0 + (w["sleeve_boost"] - 1.0) * sleeve)

    displacement = amplitude * region * patches * field
    if sleeve_folds:
        displacement += w["sleeve_fold_m"] * _sleeve_folds(flat, cfg, sleeve_folds, side)
    return posed + vertex_normals(posed, faces) * displacement[:, None]
