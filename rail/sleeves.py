"""Sleeve regions and sleeve poses baked into a garment's rest shape.

A pose swings the sleeve forward from the shoulder and bends it at the elbow,
blended like a two-bone skin so the cloth stays continuous. Pure numpy on
(n, 3) arrays in garment space (x across, y toward the back, z up).
"""

import math

import numpy as np

SIDES = (("pos", 1.0), ("neg", -1.0))


def _ramp(values, start, width):
    t = np.clip((values - start) / width, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def membership(flat, rig):
    """How much each vertex belongs to a sleeve, read on the flat-lay pose."""
    x, z = flat[:, 0], flat[:, 2]
    blend = rig["blend_m"]
    return _ramp(np.abs(x), rig["sleeve_inner_x"], blend) * _ramp(-z, -rig["sleeve_root"][1] - blend, 2 * blend)


def axis(flat, rig, sign):
    """Position along one sleeve, 0 at the shoulder seam and 1 at the cuff, and its length."""
    (rx, rz), (tx, tz) = rig["sleeve_root"], rig["sleeve_tip"]
    root = np.array([sign * rx, rz])
    direction = np.array([sign * tx, tz]) - root
    length = float(np.linalg.norm(direction))
    rel = np.stack([flat[:, 0], flat[:, 2]], 1) - root
    return rel @ (direction / length) / length, length


def follow(flat, posed, x, z):
    """Where a flat-lay point (x, z) ended up in the posed garment."""
    distance = (flat[:, 0] - x) ** 2 + (flat[:, 2] - z) ** 2
    nearest = np.argpartition(distance, 16)[:16]
    return posed[nearest].mean(axis=0)  # front and back copies: y averages out


def _rotate_x(points, pivot, angle):
    """Rotate around the x axis through `pivot`; a negative angle swings a hanging part forward (-y)."""
    c, s = math.cos(angle), math.sin(angle)
    y, z = points[:, 1] - pivot[1], points[:, 2] - pivot[2]
    out = points.copy()
    out[:, 1] = pivot[1] + c * y - s * z
    out[:, 2] = pivot[2] + s * y + c * z
    return out


def bend(posed, flat, rig, pose):
    """Swing and bend the sleeves given in `pose` ({side: {forward_deg, elbow_deg}})."""
    out = posed.copy()
    member = membership(flat, rig)
    (rx, rz), (tx, tz) = rig["sleeve_root"], rig["sleeve_tip"]
    for side, sign in SIDES:
        p = pose.get(side)
        if not p:
            continue
        t, _ = axis(flat, rig, sign)
        lower = _ramp(t, 0.4, 0.2)
        shoulder = follow(flat, posed, sign * rx, rz)
        elbow = follow(flat, posed, sign * (rx + tx) / 2, (rz + tz) / 2)
        forward = -math.radians(p["forward_deg"])
        upper_pos = _rotate_x(posed, shoulder, forward)
        lower_pos = _rotate_x(_rotate_x(posed, elbow, -math.radians(p["elbow_deg"])), shoulder, forward)
        bent = upper_pos + lower[:, None] * (lower_pos - upper_pos)
        w = (member * (sign * flat[:, 0] > 0))[:, None]
        out += w * (bent - posed)
    return out


def counterparts(flat):
    """Index of each vertex's twin on the other face (contour vertices are their own twin)."""
    key = np.round(flat[:, [0, 2]] / 1e-6).astype(np.int64)
    order = np.lexsort((key[:, 1], key[:, 0]))
    ordered = key[order]
    same = np.all(ordered[1:] == ordered[:-1], axis=1)
    pair = np.arange(len(flat))
    a, b = order[:-1][same], order[1:][same]
    pair[a], pair[b] = b, a
    return pair


def cuffs_and_fullness(posed, flat, pair, cfg, fullness):
    """Rib cuffs and per-sleeve fullness, scaling the thickness about the sleeve's mid-plane.

    Above the cuff the sleeve fabric puffs slightly, then the ribbed cuff is
    narrower and squarer: its edges round over `cuff_round_m` instead of the
    garment's wide `round_m`, so the sleeve no longer ends like a sausage.
    """
    rig, s = cfg["fabric"]["rig"], cfg["sleeves"]
    thickness, round_m = cfg["garment"]["thickness_m"], cfg["garment"]["round_m"]
    member = membership(flat, rig)
    mid = 0.5 * (posed + posed[pair])

    # Distance to the silhouette, recovered from the quarter-circle inflation height.
    h_rel = np.clip(np.abs(flat[:, 1]) / thickness, 0.0, 1.0)
    distance = (1.0 - np.sqrt(1.0 - h_rel**2)) * round_m
    square = np.sqrt(1.0 - (1.0 - np.clip(distance / s["cuff_round_m"], 0.0, 1.0)) ** 2)
    squarer = np.where(h_rel > 1e-3, square / np.maximum(h_rel, 1e-3), 1.0)

    factor = np.ones(len(flat))
    for side, sign in SIDES:
        t, _ = axis(flat, rig, sign)
        on = member * (sign * flat[:, 0] > 0)
        start = s["cuff_start"]
        puff = s["cuff_puff"] * np.exp(-(((t - (start - 0.04)) / 0.035) ** 2))
        cuff = _ramp(t, start - 0.006, 0.012)
        f = (1.0 + puff) * (1.0 - cuff * (1.0 - s["cuff_scale"])) * (1.0 + cuff * (squarer - 1.0))
        f *= fullness.get(side, 1.0) * (1.0 - cuff) + cuff  # fullness spares the cuff
        factor += on * (f - 1.0)
    return mid + (posed - mid) * factor[:, None]
