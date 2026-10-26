"""Hood down and open neckline, shaped from the flat-lay geometry.

The flat-lay photo shows the hood standing up and a closed neckline. On a
hanger the hood falls behind as a soft bag; either side of the neck its sides
turn back over the hanger's arms like lapels, their front edges running
diagonally down to the V, and between them the inner back of the neck shows
under the hanger. Four steps, all pure numpy on (n, 3)
vertex arrays in garment space (x across, y through the garment toward its
back, z up), applied the same way to the render mesh and the cloth proxy:

- fold_line: the height where the sheet stops standing and turns back, per
  vertex: low in the face opening, rising up the hood's sides to the
  hanger's arms, then down along them toward the shoulders;
- taper: give the neck and hood their thickness, side by side (flush with the
  front up to the fold, thin through the crease, a ridged bag behind, thin
  inner side);
- recess: push the lining inside the face opening (widening up the front
  edges) back against the back panel;
- fold: bend everything above the fold line backward along a curve whose
  angle grows with the distance from it (a tight crease lying on the hanger,
  then the bag hangs down resting on the back, its lower edge a smooth curve
  down to the center seam). Per-garment `shift_m`, `drop` and `puff` make
  each hood fall a little off-square.
"""

import numpy as np


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def hinge_z(cfg, photo):
    """Garment-space height of the neck seam, from its row on the front photo."""
    fy0 = photo["front_bbox"][2]
    m = float(photo["m_per_px"])
    return -(cfg["hood"]["hinge_px"] - fy0) * m - cfg["garment"]["hook_clearance_m"]


def fold_line(x, z_hinge, hood, hanger_top=None, shoulder=None):
    """Garment-space height where the sheet turns back, per vertex.

    The flat-lay face opening is a V between the hood's front edges, lined
    inside. Within it (`open_half_width_m`) the hood folds `center_m` above the
    neck seam: the lining goes behind, the inner back of the neck stays under
    the hanger. Beside it the line rises toward `ear_m` over `ramp_m`, up the
    hood's sides. `hanger_top` (|x| -> z) caps it `cover_m` from the hanger's
    upper edge (negative: below it, by about the height of the turned-back
    crease), so each side folds over the arm like a lapel: its top sits on the
    arm where the two meet, the arm going in under it, then follows the arm
    down to the shoulder. Without a hanger the sides stand up to `ear_m`.
    `shoulder` (|x| -> z, the garment's top edge) keeps the fold
    `shoulder_clear_m` above the shoulders past `shoulder_from_m`: only the
    hood turns back, not the top of the shoulder beside it.
    """
    s = hood["stand"]
    ax = np.abs(x)
    rise = _smoothstep((ax - s["open_half_width_m"]) / s["ramp_m"])
    z = s["center_m"] + (s["ear_m"] - s["center_m"]) * rise
    if hanger_top is not None:
        # Smooth minimum: the top of the side rounds over onto the arm (`cap_round_m`).
        # Past the ramp it sits a little higher on the arm (`cover_slope`, up
        # to `cover_max_m`): the turned-back hood is fuller there than at the
        # front edge. Down at the shoulder it is back on the arm, where the
        # shoulder floor below takes over.
        kc = s["cap_round_m"]
        out = kc * np.logaddexp(0.0, (ax - s["open_half_width_m"] - s["ramp_m"]) / kc)
        bulk = s["cover_max_m"] * np.tanh(s["cover_slope"] * out / s["cover_max_m"])
        bulk *= 1.0 - _smoothstep((ax - s["level_from_m"]) / s["level_len_m"])
        b = hanger_top(ax) + s["cover_m"] + bulk - z_hinge
        h = np.clip(0.5 + 0.5 * (b - z) / kc, 0.0, 1.0)
        z = b + (z - b) * h - kc * h * (1.0 - h)
    floor = np.zeros_like(ax)
    if shoulder is not None:
        clear = shoulder(ax) + s["shoulder_clear_m"] - z_hinge
        floor = np.maximum(clear, 0.0) * _smoothstep((ax - s["shoulder_from_m"]) / 0.01)
    # Soft floor, tight enough not to lift the fold where it is well above.
    kf = 0.002
    return z_hinge + floor + kf * np.logaddexp(0.0, (z - floor) / kf)


def tuck(co, flat, hood, shoulder):
    """Pull the hood's flare over the shoulders in against the side of the neck.

    On the flat lay the bottom of the hood spreads past the neckline seam,
    over the shoulders. Hung up, that strip belongs to the side of the
    standing hood; left where it is, it folds into a thin fin above the
    shoulder. Past `tuck_from_m`, everything above the shoulder line (`shoulder`,
    |x| -> z, plus `shoulder_clear_m`, over `tuck_rise_m`) keeps only
    `tuck_keep` of its width. `flat` gives the regions, `co` is moved.
    """
    s = hood["stand"]
    ax = np.abs(flat[:, 0])
    above = _smoothstep((flat[:, 2] - shoulder(ax) - s["shoulder_clear_m"]) / s["tuck_rise_m"])
    out = co.copy()
    out[:, 0] -= np.sign(flat[:, 0]) * np.maximum(ax - s["tuck_from_m"], 0.0) * (1.0 - s["tuck_keep"]) * above
    return out


def edge_distance(co, z_fold):
    """Distance in the sheet plane (x, z) from each hood vertex to the hood's free edge.

    The edge is the welded contour (y = 0) above the fold line (`z_fold`, a
    height or one per vertex); vertices below it get infinity.
    """
    above = co[:, 2] > z_fold
    rim = co[(np.abs(co[:, 1]) < 1e-6) & above][:, [0, 2]]
    out = np.full(len(co), np.inf)
    hood = np.nonzero(above)[0]
    if len(rim) == 0:
        return out
    for chunk in np.array_split(hood, max(1, len(hood) // 2000)):
        dx = co[chunk, 0, None] - rim[None, :, 0]
        dz = co[chunk, 2, None] - rim[None, :, 1]
        out[chunk] = np.sqrt((dx * dx + dz * dz).min(axis=1))
    return out


def taper(co, z_hinge, hood, volume=True, inflation=None, z_fold=None):
    """Thickness of the neck and hood, per side of the sheet.

    - Front side, up to the fold line: only slightly thinned
      (`neck_front_scale`), so the standing sides and the folded hood start
      flush with the front of the garment.
    - Back side: thinned to fabric thickness (`neck_scale`); once folded it is
      the inner side of the hood, resting against the back.
    - Front side of the hood (the outer side once folded): flush at the fold,
      down to `fold_scale` over `fold_thin_m` so the crease stays tight on the
      hanger, then the bag. The bag is deepest along the middle, where the
      center seam runs, and recedes linearly toward the hood's edges
      (`bag_reach_m`, `bag_exponent`): a ridge across, and in profile a
      triangle, deepest halfway down and pointed at the bottom. `seam_depth`
      pinches the seam itself. The flat-lay photo squashed this depth.

    `volume=False` keeps the hood as thick as the seam (used by the cloth
    drape). `inflation` = (thickness_m, round_m) of the photo inflation is
    required with `volume`: the bag depth is set in meters and its edge rounds
    over `edge_round_m` instead of the garment's wide `round_m`. `z_fold` is
    the fold line (see `fold_line`); the neck seam by default.
    """
    x, y, z = co.T
    z_fold = z_hinge if z_fold is None else z_fold
    central = 1.0 - _smoothstep((np.abs(x) - hood["taper_half_width_m"]) / 0.04)
    front = y < 0.0
    near = _smoothstep((z - (z_hinge - hood["taper_m"])) / hood["taper_m"])
    below = 1.0 - (1.0 - np.where(front, hood["neck_front_scale"], hood["neck_scale"])) * near
    u = (z - z_fold) * hood["length_scale"]
    roll = hood["neck_front_scale"]
    outer = np.full(len(co), roll)
    if volume:
        thickness, _ = inflation
        crease = hood["fold_scale"] + (roll - hood["fold_scale"]) * (1.0 - _smoothstep(u / hood["fold_thin_m"]))
        d = edge_distance(co, z_fold)
        ridge = np.clip(d / hood["bag_reach_m"], 0.0, 1.0) ** hood["bag_exponent"]
        edge = np.sqrt(1.0 - (1.0 - np.clip(d / hood["edge_round_m"], 0.0, 1.0)) ** 2)
        seam = 1.0 - hood["seam_depth"] * np.exp(-((x / hood["seam_width_m"]) ** 2))
        depth = hood["bag_m"] * (1.0 + hood.get("puff", 0.0) * x / 0.15) * ridge * edge * seam
        # Depth in meters -> factor on the inflation height (zero on the contour).
        h = np.abs(y)
        bag = np.where(h > 1e-6, depth / np.maximum(h, 1e-6), 0.0)
        grow = _smoothstep((u - hood["bag_start_m"]) / hood["bag_len_m"])
        outer = crease + (bag - crease) * grow
    above = np.where(front, outer, hood["neck_scale"])
    scale = np.where(u > 0.0, above, below)
    out = co.copy()
    out[:, 1] *= 1.0 - (1.0 - scale) * central
    return out


def recess(co, shaped, z_hinge, hood, z_fold=None):
    """Push the lining inside the face opening back to the inside of the back,
    and roll the hood's front edges along it.

    The opening is a V from `depth_m` below the neck seam, `half_width_m` wide
    at the seam (`exponent` shapes its sides; wider above), up to where the
    lining folds back (`stand.center_m`). Above that it keeps widening by
    `flare_m` per meter of height: the front edges run on diagonally up the
    standing sides, like lapels, and the lining between them stays pushed back
    up to the fold line (`z_fold`, per vertex; `stand.center_m` by default).
    Just outside it, each front edge of the hood is a thick hem: it bulges
    `roll_m` toward the front, centered `roll_offset_m` from the opening over
    `roll_width_m`, from the V up to the top of the standing side.
    """
    r = hood["recess"]
    x, y, z = shaped.T
    z_bottom = z_hinge - r["depth_m"]
    z_top = z_hinge + hood["stand"]["center_m"]
    z_fold = z_top if z_fold is None else z_fold
    width = r["half_width_m"] * ((np.clip(z, z_bottom, z_top) - z_bottom) / r["depth_m"]) ** r["exponent"]
    if r.get("flare_max_m"):
        width = width + r["flare_max_m"] * np.tanh(r["flare"] * np.maximum(z - z_top, 0.0) / r["flare_max_m"])
    soft = r["soft_m"]
    front = y < 0.0
    w = (
        _smoothstep((width - np.abs(x)) / soft)
        * _smoothstep((z - z_bottom) / soft)
        * (1.0 - _smoothstep((z - z_fold) / soft))
        * front
    )
    out = co.copy()
    # Target: just in front of the back panel, which mirrors the front (y -> -y).
    out[:, 1] += w * (-2.0 * y - r["gap_m"])
    if r.get("roll_m"):
        across = (np.abs(x) - width - r["roll_offset_m"]) / r["roll_width_m"]
        along = _smoothstep((z - z_bottom + soft) / (2.0 * soft)) * (
            1.0 - _smoothstep((z - (z_hinge + hood["stand"]["ear_m"])) / (2.0 * soft))
        )
        # Scaled by the local thickness, so the welded rim stays closed.
        h = np.clip(-y / r["roll_ref_m"], 0.0, 1.0)
        out[:, 1] -= r["roll_m"] * np.exp(-across * across) * along * h * front
    return out


def _fold_curve(u_max, hood):
    """Tangent angle and mid-plane position along the folded sheet, tabulated."""
    us = np.linspace(0.0, max(u_max, 1e-3), 4096)
    phi = (
        np.radians(hood["crease_deg"]) * _smoothstep(us / hood["crease_len_m"])
        + np.radians(hood["fold_deg"]) * _smoothstep(us / hood["fold_len_m"])
        + np.radians(hood["tuck_deg"]) * _smoothstep((us - hood["fold_len_m"]) / hood["tuck_len_m"])
    )
    du = np.diff(us)
    mid_y = np.concatenate([[0.0], np.cumsum(0.5 * (np.sin(phi[1:]) + np.sin(phi[:-1])) * du)])
    mid_z = np.concatenate([[0.0], np.cumsum(0.5 * (np.cos(phi[1:]) + np.cos(phi[:-1])) * du)])
    return us, phi, mid_y, mid_z


def _hang_scale(x, y, z, z_start, z_hinge, level, hood):
    """Factor on the length along the sheet, per vertex of the turned-back hood,
    so that its lower edge seen from behind follows the `hang` outline.

    Left alone, each column hangs as far as the flat-lay hood is tall above its
    fold line: the crown lowest in the middle, a notch either side where the
    lapels fold higher, and the hood's sides hanging down as tabs. A real hood
    hangs as one bag tapering to its center seam: a parabola `bottom_m` below
    the neck seam in the middle, rising by `curve` x² toward the shoulders.
    A column's natural length runs up to the hood's outline (the welded rim,
    y = 0), read across in `cell_m` bins. `level` (per vertex) is how far the
    shoulder levelling in `fold` lowers it.
    """
    g = hood["hang"]
    us, _, _, mid_z = _fold_curve(1.0, hood)
    peak = np.argmax(mid_z)
    rim = np.abs(y) < 1e-6
    bins = np.round(x[rim] / g["cell_m"]).astype(int)
    first = bins.min()
    top = np.full(bins.max() - first + 1, -np.inf)
    np.maximum.at(top, bins - first, z[rim])
    centers = (np.arange(len(top)) + first) * g["cell_m"]
    filled = np.isfinite(top)
    length = (np.interp(x, centers[filled], top[filled]) - z_start) * hood["length_scale"]
    bottom = z_hinge + g["bottom_m"] + g["curve"] * x * x
    # Past the crease the sheet hangs straight down: invert its height along the way.
    end = bottom - z_start + level * mid_z[peak]
    target = np.interp(-end, -mid_z[peak:], us[peak:])
    # Columns barely above the fold (the hood's corners) keep a sane stretch.
    return np.clip(target / np.maximum(length, 1e-6), 0.25, 2.0)


def _inner_thickness(x, y, z, along, cell=0.004):
    """Thickness of the hood's inner side (y > 0) at each vertex's place on the flat sheet.

    Read from the inner vertices more than 5 cm past the fold line (`along`,
    flat distance; nearer the fold the recess moves front vertices to y > 0),
    binned on the sheet, then carried up each column toward the fold and
    across to columns without any.
    """
    ix = np.round(x / cell).astype(int)
    iz = np.round(z / cell).astype(int)
    ix -= ix.min()
    iz -= iz.min()
    grid = np.full((ix.max() + 1, iz.max() + 1), -np.inf)
    inside = (y > 0.0) & (along > 0.05)
    np.maximum.at(grid, (ix[inside], iz[inside]), y[inside])
    rows = np.arange(grid.shape[1])
    filled = np.zeros(grid.shape[0], dtype=bool)
    for i, column in enumerate(grid):
        ok = np.isfinite(column)
        if ok.any():
            grid[i] = np.interp(rows, rows[ok], column[ok])
            filled[i] = True
    cols = np.arange(grid.shape[0])
    if not filled.any():
        return np.zeros_like(x)
    for j in rows:
        grid[:, j] = np.interp(cols, cols[filled], grid[filled, j])
    return grid[ix, iz]


def back_surface(posed, shaped, z_hinge, cell=0.01):
    """y of the garment's back below the seam, as a function of (x, z), blurred.

    Returns a lookup taking x and z arrays. Heights above the seam read the
    top row.
    """
    xs = np.arange(-0.2, 0.2 + cell / 2, cell)
    zs = np.arange(z_hinge - 0.32, z_hinge - cell / 2, cell)
    back = (shaped[:, 1] > 0.0) & (shaped[:, 2] < z_hinge)
    x, y, z = posed[back].T
    ix = np.round((x - xs[0]) / cell).astype(int)
    iz = np.round((z - zs[0]) / cell).astype(int)
    keep = (ix >= 0) & (ix < len(xs)) & (iz >= 0) & (iz < len(zs))
    grid = np.full((len(xs), len(zs)), -np.inf)
    np.maximum.at(grid, (ix[keep], iz[keep]), y[keep])
    # The window lies inside the body, so every cell has vertices; guard anyway.
    grid[~np.isfinite(grid)] = np.nanmax(np.where(np.isfinite(grid), grid, np.nan))
    kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0]) / 9.0
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (2, 2)
        grid = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), axis, np.pad(grid, pad, mode="edge"))

    def lookup(px, pz):
        fx = np.clip((px - xs[0]) / cell, 0.0, len(xs) - 1.001)
        fz = np.clip((pz - zs[0]) / cell, 0.0, len(zs) - 1.001)
        i, j = fx.astype(int), fz.astype(int)
        tx, tz = fx - i, fz - j
        return (
            grid[i, j] * (1 - tx) * (1 - tz)
            + grid[i + 1, j] * tx * (1 - tz)
            + grid[i, j + 1] * (1 - tx) * tz
            + grid[i + 1, j + 1] * tx * tz
        )

    return lookup


def fold(posed, shaped, z_hinge, z_fold, hood):
    """Positions with the hood folded back.

    `shaped` is the flat garment (tapered, recessed) the fold is computed from;
    `posed` supplies every vertex below the fold line `z_fold` (per vertex, see
    `fold_line`; it may have been draped) and the back the hood lies on
    (`rest_gap_m` off it, `rest_edge_m` at the hood's edges). `hang` sets how
    far each column hangs (see `_hang_scale`).
    """
    out = posed.copy()
    mask = shaped[:, 2] > z_fold
    x, y, z = shaped[mask].T
    z_start = z_fold[mask]
    st = hood["stand"]
    level = np.zeros_like(x)
    if "level_from_m" in st:
        level = _smoothstep((np.abs(x) - st["level_from_m"]) / st["level_len_m"])
    # Distance along the sheet from the fold line, shaped to the `hang` outline;
    # `drop` lets one side hang lower.
    u = (z - z_start) * hood["length_scale"]
    if "hang" in hood:
        u = u * _hang_scale(x, y, z, z_start, z_hinge, level, hood)
    u = u * (1.0 + hood.get("drop", 0.0) * x / 0.15)
    s = -y  # offset from the mid-plane, positive on the front side
    us, phi_table, mid_y_table, mid_z_table = _fold_curve(u.max(initial=0.0), hood)
    phi = np.interp(u, us, phi_table)
    mid_y = np.interp(u, us, mid_y_table)
    mid_z = np.interp(u, us, mid_z_table)
    # The front side's normal turns with the sheet: (-1, 0) at the seam, (+1, 0) once folded.
    lean = hood["lean_m"] * _smoothstep(u / hood["lean_len_m"])
    # A hood never falls quite square: the hanging part slides to one side.
    px = x + hood.get("shift_m", 0.0) * _smoothstep(u / 0.1)
    pz = z_start + mid_z
    if "level_from_m" in st:
        # Down at the shoulder, the side of the hood turns back level with the
        # fold instead of rising into a crest: no lip standing on the shoulder.
        peak = us[np.argmax(mid_z_table)]
        pz = pz - level * mid_z_table.max() * _smoothstep(u / peak)
    if "rest_gap_m" in hood:
        # Once hanging, the inner side lies on the back: pushed out where it
        # would sink into it, drawn in where it would float off it (around the
        # shoulders the back curves away forward).
        back = back_surface(posed, shaped, z_hinge)
        # Thickness of the inner side where each vertex lies: it thins out to
        # the hood's edges, which then lie on the back too.
        inner = _inner_thickness(x, y, z, z - z_start)
        # Toward its edges the hood settles into the back (`rest_edge_m`, below
        # zero): fabric lying on fabric, with no crevice under the edge to
        # catch a dark line of shadow, seen from the side.
        thin = 1.0 - inner / max(inner.max(), 1e-6)
        edge_gap = hood.get("rest_edge_m", hood["rest_gap_m"])
        gap = hood["rest_gap_m"] + (edge_gap - hood["rest_gap_m"]) * thin
        need = back(px, pz) + gap + inner - (mid_y + lean)
        lean = lean + need * _smoothstep((phi - np.pi / 2) / (np.pi / 2))
        # The standing sides carry the garment's back up toward the fold, so the
        # hanging hood rests against them instead of leaving a slot between the
        # two (seen in profile). Thin again just under the fold (`fill_fade_m`),
        # where the sheet turns over.
        sx, sy, sz = shaped.T
        side = _smoothstep((np.abs(sx) - st["open_half_width_m"]) / st["ramp_m"])
        rise = _smoothstep((sz - (z_hinge - st["fill_rise_m"])) / st["fill_rise_m"])
        fade = 1.0 - _smoothstep((sz - (z_fold - st["fill_fade_m"])) / st["fill_fade_m"])
        fill = ~mask & (sy > 0.0)
        target = back(sx[fill], np.minimum(sz[fill], z_hinge))
        out[fill, 1] += (side * rise * fade)[fill] * np.maximum(target - out[fill, 1], 0.0)
    out[mask, 0] = px
    out[mask, 1] = mid_y - s * np.cos(phi) + lean
    out[mask, 2] = pz + s * np.sin(phi)
    return out
