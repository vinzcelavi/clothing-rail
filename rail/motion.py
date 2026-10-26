"""Rail choreography and fabric secondary motion.

The whole rail is driven by one scalar, the scroll position `s` (in slot
units), eased between featured garments with a cubic-bezier curve, and by a
"spotlight" amount `a` (0 = every garment in profile, 1 = the garment under
`s` faces the camera). Every other quantity derives from those two, the way a
cover-flow does:

- facing of slot k = a * window(k - s), for featured slots only: the
  fillers between them slide past in profile
- slot position = profile spacing x (k - s), each garment a little off the
  even spacing, plus room ramping in around the spotlight, so position `s`
  sits at x = 0
- swivel angle = (1 - facing) * profile angle

On top of that rigid motion, damped springs add the secondary motion: the
hanger swinging on the rail, and the fabric (chest, hem, sleeves) lagging,
twisting and settling. All of it fades out shortly after each move so the
holds are perfectly still.

Pure Python, no bpy, so it can be run and checked outside Blender:

    python3 -m rail.motion [config/rail.json]
"""

import math
import random
from dataclasses import dataclass

from . import config as rail_config

GRAVITY = 9.81
BONES = ("chest", "hem", "sleeve_pos", "sleeve_neg")


def clamp01(t):
    return 0.0 if t < 0.0 else 1.0 if t > 1.0 else t


def smoothstep(t):
    t = clamp01(t)
    return t * t * (3.0 - 2.0 * t)


class CubicBezier:
    """CSS / After Effects style cubic-bezier(x1, y1, x2, y2) easing."""

    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    @staticmethod
    def _coord(t, p1, p2):
        return 3.0 * (1.0 - t) ** 2 * t * p1 + 3.0 * (1.0 - t) * t * t * p2 + t**3

    @staticmethod
    def _slope(t, p1, p2):
        return 3.0 * (1.0 - t) ** 2 * p1 + 6.0 * (1.0 - t) * t * (p2 - p1) + 3.0 * t * t * (1.0 - p2)

    def __call__(self, u):
        if u <= 0.0:
            return 0.0
        if u >= 1.0:
            return 1.0
        t = u
        for _ in range(8):
            error = self._coord(t, self.x1, self.x2) - u
            slope = self._slope(t, self.x1, self.x2)
            if abs(error) < 1e-7 or abs(slope) < 1e-6:
                break
            t -= error / slope
        if not 0.0 <= t <= 1.0 or abs(self._coord(t, self.x1, self.x2) - u) > 1e-6:
            lo, hi = 0.0, 1.0
            for _ in range(60):
                t = (lo + hi) / 2.0
                if self._coord(t, self.x1, self.x2) < u:
                    lo = t
                else:
                    hi = t
        return self._coord(t, self.y1, self.y2)


@dataclass
class Segment:
    start: float
    duration: float
    s_from: float
    s_to: float
    a_from: float
    a_to: float

    @property
    def end(self):
        return self.start + self.duration


@dataclass
class Frame:
    x: list[float]  # slot position along the rail, camera-centered
    sway: list[float]  # hanger swing around the rail (Y), positive = bottom toward -X
    swivel: list[float]  # radians around Z, 0 = facing the camera
    facing: list[float]
    bones: dict[str, list[tuple[float, float, float]]]  # bone-local XYZ Euler per slot


class Spring:
    """Damped angular spring chasing a target angle."""

    def __init__(self, freq_hz, damping):
        self.omega = 2.0 * math.pi * freq_hz
        self.zeta = damping
        self.angle = 0.0
        self.velocity = 0.0

    def step(self, target, dt):
        accel = self.omega**2 * (target - self.angle) - 2.0 * self.zeta * self.omega * self.velocity
        self.velocity += accel * dt
        self.angle += self.velocity * dt

    def reset(self):
        self.angle = self.velocity = 0.0


def soft_limit(angle, max_deg):
    limit = math.radians(max_deg)
    return limit * math.tanh(angle / limit)


class Choreography:
    def __init__(self, cfg):
        timing, rail = cfg["timing"], cfg["rail"]
        self.cfg = cfg
        self.n_slots = len(rail_config.slots(cfg))
        self.profile_gap = rail["profile_gap_m"]
        self.front_gap = rail["front_gap_m"]
        self.profile_angle = math.radians(rail["profile_angle_deg"])
        self.window = rail["turn_window"]
        self.space_window = rail["space_window"]
        self.plateau = rail["turn_plateau"]
        self.ease = CubicBezier(*rail["easing"])

        items = rail_config.featured(cfg)
        self.featured = set(items)
        # A real rail is never evenly spaced: each hanger sits up to
        # `gap_jitter_m` off its place, the same on every run of a seed.
        rng = random.Random(f"{cfg['variation']['seed']}-rail")
        jitter = rail.get("gap_jitter_m", 0.0)
        self.offsets = [rng.uniform(-jitter, jitter) for _ in range(self.n_slots)]
        first, last = float(items[0]), float(items[-1])
        self.segments = []
        self.holds = []
        t = timing["intro_hold_s"]
        self.initial = (first - rail["intro_offset"], 0.0)
        self.segments.append(Segment(t, timing["intro_turn_s"], first - rail["intro_offset"], first, 0.0, 1.0))
        t += timing["intro_turn_s"]
        for i, slot in enumerate(items):
            self.holds.append((t, t + timing["hold_s"]))
            t += timing["hold_s"]
            if i + 1 < len(items):
                # Sliding past fillers takes longer than stepping to the next slot.
                duration = timing["turn_s"] + timing.get("slide_per_slot_s", 0.0) * (items[i + 1] - slot - 1)
                self.segments.append(Segment(t, duration, float(slot), float(items[i + 1]), 1.0, 1.0))
                t += duration
        self.segments.append(Segment(t, timing["outro_turn_s"], last, last + rail["outro_offset"], 1.0, 0.0))
        self.duration = t + timing["outro_turn_s"] + timing["outro_hold_s"]

    def scroll(self, t):
        """Scroll position (slot units) and spotlight amount at time t."""
        s, a = self.initial
        for seg in self.segments:
            if t < seg.start:
                break
            e = self.ease(clamp01((t - seg.start) / seg.duration))
            s = seg.s_from + (seg.s_to - seg.s_from) * e
            a = seg.a_from + (seg.a_to - seg.a_from) * e
        return s, a

    def facing(self, s, a):
        out = []
        for k in range(self.n_slots):
            if k not in self.featured:
                out.append(0.0)
                continue
            d = abs(k - s)
            out.append(a * (1.0 - smoothstep((d - self.plateau) / (self.window - self.plateau))))
        return out

    def positions(self, s, a):
        """Slot X positions, camera-centered on the scroll position.

        Position is a function of the offset d = k - s that only ever increases
        with d: profile spacing everywhere, plus extra room that ramps in (sine,
        flat at the window edge) over |d| <= space_window while a garment faces
        the camera. A space window a bit wider than the turn window makes room
        before the incoming garment swings its sleeves out. Every slot therefore slides in
        the same direction, on the same easing, without the stop-and-go that
        widening the gaps from the facing amounts produced. Each slot's offset
        from the even spacing is measured from the offset under `s`, eased
        between slots so the slide keeps a smooth speed.
        """
        extra = a * (self.front_gap - self.profile_gap)
        i = min(max(int(math.floor(s)), 0), self.n_slots - 2)
        here = self.offsets[i] + (self.offsets[i + 1] - self.offsets[i]) * smoothstep(s - i)
        xs = []
        for k in range(self.n_slots):
            d = k - s
            ramp = math.sin(math.pi / 2 * clamp01(abs(d) / self.space_window))
            xs.append(self.profile_gap * d + self.offsets[k] - here + math.copysign(extra * ramp, d))
        return xs

    def rigid(self, t):
        """Positions, swivel angles and facing at time t, before any spring."""
        s, a = self.scroll(t)
        facing = self.facing(s, a)
        xs = self.positions(s, a)
        swivel = [(1.0 - f) * self.profile_angle for f in facing]
        return xs, swivel, facing

    def envelope(self, t, settle):
        """1 during a move, fading to 0 within `settle` seconds after it ends."""
        last_end = None
        for seg in self.segments:
            if t < seg.start:
                break
            if t <= seg.end:
                return 1.0
            last_end = seg.end
        if last_end is None:
            return 0.0
        return 1.0 - smoothstep((t - last_end) / settle)

    def frame_count(self, fps):
        return int(math.ceil(self.duration * fps))

    def hold_frames(self, fps):
        """Blender frame number in the middle of each featured hold."""
        return [int(round((start + end) / 2 * fps)) + 1 for start, end in self.holds]

    def bake(self, fps, substeps=8):
        """Simulate the whole timeline; returns one Frame per Blender frame (frame 1 = t 0)."""
        sway_cfg, fabric = self.cfg["sway"], self.cfg["fabric"]
        settle = sway_cfg["settle_s"]
        n = self.n_slots
        dt = 1.0 / (fps * substeps)

        sway = [Spring(math.sqrt(GRAVITY / sway_cfg["length_m"]) / (2 * math.pi), sway_cfg["damping"]) for _ in range(n)]

        def springs(name):
            return [Spring(fabric[name]["freq_hz"], fabric[name]["damping"]) for _ in range(n)]

        chest_x, chest_y, hem_x, hem_y = springs("chest"), springs("chest"), springs("hem"), springs("hem")
        chest_twist, hem_twist = springs("twist"), springs("twist")
        sleeves = {sign: (springs("sleeve"), springs("sleeve")) for sign in (1, -1)}
        everything = [sway, chest_x, chest_y, hem_x, hem_y, chest_twist, hem_twist] + [s for pair in sleeves.values() for s in pair]

        sleeve_r = fabric["rig"]["sleeve_root"][0]
        rest_in = math.radians(fabric["sleeve_rest"]["in_deg"])
        rest_forward = math.radians(fabric["sleeve_rest"]["forward_deg"])

        history = [self.rigid(-dt), self.rigid(0.0)]
        frames = []
        steps = (self.frame_count(fps) - 1) * substeps
        for step in range(steps + 1):
            t = step * dt
            env = self.envelope(t, settle)
            (x_prev, th_prev, _), (x_cur, th_cur, facing) = history
            x_next, th_next, facing_next = self.rigid(t + dt)

            if step % substeps == 0:
                frames.append(self._frame(x_cur, th_cur, facing, env, sway, chest_x, chest_y, chest_twist,
                                          hem_x, hem_y, hem_twist, sleeves, rest_in, rest_forward))

            for k in range(n):
                accel = (x_next[k] - 2.0 * x_cur[k] + x_prev[k]) / (dt * dt)
                omega = (th_next[k] - th_prev[k]) / (2.0 * dt)
                alpha = (th_next[k] - 2.0 * th_cur[k] + th_prev[k]) / (dt * dt)
                # Rail acceleration seen in the garment's own frame (x across the chest, y through it).
                cos_t, sin_t = math.cos(th_cur[k]), math.sin(th_cur[k])
                a_lx, a_ly = accel * cos_t, -accel * sin_t

                sway[k].step(sway_cfg["gain"] * accel / GRAVITY, dt)
                g_chest, g_hem = fabric["chest"]["gain"], fabric["hem"]["gain"]
                chest_x[k].step(-g_chest * a_lx / GRAVITY, dt)
                chest_y[k].step(-g_chest * a_ly / GRAVITY, dt)
                hem_x[k].step(-g_hem * a_lx / GRAVITY, dt)
                hem_y[k].step(-g_hem * a_ly / GRAVITY, dt)
                # The hem lags behind the hanger's spin, then catches up.
                twist_target = -fabric["twist"]["gain"] * alpha / chest_twist[k].omega**2
                chest_twist[k].step(twist_target, dt)
                hem_twist[k].step(twist_target, dt)
                g_sleeve = fabric["sleeve"]["gain"]
                for sign, (sx, sy) in sleeves.items():
                    # Spin flings the sleeves outward and drags them behind the turn.
                    sx[k].step(-g_sleeve * (a_lx - sign * omega * omega * sleeve_r) / GRAVITY, dt)
                    sy[k].step(-g_sleeve * (a_ly + sign * alpha * sleeve_r) / GRAVITY, dt)

            if env == 0.0:
                for group in everything:
                    for spring in group:
                        spring.reset()
            history = [history[1], (x_next, th_next, facing_next)]
        return frames

    def _frame(self, xs, swivel, facing, env, sway, chest_x, chest_y, chest_twist,
               hem_x, hem_y, hem_twist, sleeves, rest_in, rest_forward):
        fabric = self.cfg["fabric"]
        n = self.n_slots

        def lim(spring, group, k):
            return soft_limit(spring[k].angle, fabric[group]["max_deg"]) * env

        bones = {name: [] for name in BONES}
        for k in range(n):
            # Bone axes (checked in Blender): local X tilts the tip toward +Y,
            # local Z tilts it toward -X, local Y twists around the bone.
            bones["chest"].append((lim(chest_y, "chest", k), -lim(chest_twist, "twist", k), -lim(chest_x, "chest", k)))
            bones["hem"].append((lim(hem_y, "hem", k), -lim(hem_twist, "twist", k), -lim(hem_x, "hem", k)))
            for sign, name in ((1, "sleeve_pos"), (-1, "sleeve_neg")):
                sx, sy = sleeves[sign]
                toward_x = lim(sx, "sleeve", k) - sign * rest_in
                toward_y = lim(sy, "sleeve", k) - rest_forward
                bones[name].append((toward_y, 0.0, -toward_x))
        return Frame(
            x=list(xs),
            sway=[s.angle * env for s in sway],
            swivel=list(swivel),
            facing=list(facing),
            bones=bones,
        )


# Garment footprint seen from above, measured on the built mesh: the body is
# wide and thin, the hood hanging behind is narrow and deep.
FOOTPRINT = (
    (0.30, -0.04, 0.045),  # body: half width, front, back
    (0.11, -0.035, 0.085),  # hood
)


def min_clearance(chore, frames, footprint=FOOTPRINT):
    """Smallest gap between neighbouring garments (negative = they intersect)."""

    def corners(x, theta, part):
        half, front, back = part
        c, s = math.cos(theta), math.sin(theta)
        return [(x + u * c - v * s, u * s + v * c) for u in (-half, half) for v in (front, back)]

    def separation(pa, pb, theta_a, theta_b):
        # Separating-axis distance between two oriented rectangles.
        best = -math.inf
        for theta in (theta_a, theta_b):
            for axis in ((math.cos(theta), math.sin(theta)), (-math.sin(theta), math.cos(theta))):
                a = [axis[0] * x + axis[1] * y for x, y in pa]
                b = [axis[0] * x + axis[1] * y for x, y in pb]
                best = max(best, max(min(b) - max(a), min(a) - max(b)))
        return best

    worst = (math.inf, None)
    for number, frame in enumerate(frames, start=1):
        for k in range(chore.n_slots - 1):
            ta, tb = frame.swivel[k], frame.swivel[k + 1]
            gap = min(
                separation(corners(frame.x[k], ta, a), corners(frame.x[k + 1], tb, b), ta, tb)
                for a in footprint
                for b in footprint
            )
            if gap < worst[0]:
                worst = (gap, (number, k))
    return worst


if __name__ == "__main__":
    import sys

    cfg = rail_config.load(sys.argv[1] if len(sys.argv) > 1 else None)
    fps = cfg["output"]["fps"]
    chore = Choreography(cfg)
    frames = chore.bake(fps)
    print(f"{chore.duration:.2f} s, {len(frames)} frames at {fps} fps, holds at frames {chore.hold_frames(fps)}")
    print(f"max hanger sway {math.degrees(max(abs(a) for f in frames for a in f.sway)):.1f} deg")
    for name in BONES:
        peak = max(abs(v) for f in frames for rot in f.bones[name] for v in rot)
        print(f"max {name} {math.degrees(peak):.1f} deg")
    static = sum(1 for a, b in zip(frames, frames[1:]) if a == b)
    print(f"{static} frames identical to the previous one")
    gap, where = min_clearance(chore, frames)
    print(f"min clearance between neighbours {gap * 100:.1f} cm at frame {where[0]} (slots {where[1]}-{where[1] + 1})")
