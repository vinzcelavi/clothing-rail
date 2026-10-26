"""Render build/rail.blend to a transparent PNG sequence, plus a background plate.

A frame whose animated transforms and fabric pose (including the motion-blur
window) match the previous frame is a copy of it: the holds are rendered once,
then duplicated.

Run: blender -b build/rail.blend -P scripts/render.py -- [options]
  --preview        half resolution, few samples, into renders/preview
  --stills         only the middle frame of each hold
  --frames 90-140  frames to render, ranges inclusive (90-140,200,210)
  --resume         keep frames already on disk
  --plate          render only the background plate (wall alone) to background.png
  --samples N      override the sample count
  --scale P        override the resolution percentage
  --mp4            also write an H.264 preview over background.png (or the wall color)
  --out DIR        output directory (default renders/final or renders/preview)
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rail import config as rail_config  # noqa: E402


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="render.py")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--stills", action="store_true")
    parser.add_argument("--frames")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plate", action="store_true")
    parser.add_argument("--samples", type=int)
    parser.add_argument("--scale", type=int)
    parser.add_argument("--mp4", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--config")
    return parser.parse_args(argv)


def enable_gpu(scene):
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for device_type in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = device_type
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == device_type]
        if gpus:
            for device in prefs.devices:
                device.use = device.type == device_type
            scene.cycles.device = "GPU"
            return f"{device_type} ({', '.join(d.name for d in gpus)})"
    scene.cycles.device = "CPU"
    return "CPU"


def signature(scene, objects, rigs, frame):
    """Transforms and bone poses at the frame and at the edges of its motion-blur window."""
    values = []
    for number, subframe in ((frame - 1, 0.5), (frame, 0.0), (frame, 0.5)):
        scene.frame_set(number, subframe=subframe)
        for obj in objects:
            values.extend(round(v, 6) for row in obj.matrix_world for v in row)
        for rig in rigs:
            for bone in rig.pose.bones:
                values.extend(round(v, 6) for v in bone.rotation_euler)
    return tuple(values)


def render_plate(scene, out):
    """The wall alone, lit like the animation, opaque: the layer under the PNG sequence."""
    wall = bpy.data.objects["wall"]
    for obj in scene.objects:
        if obj.type in {"MESH", "CURVE"} and obj is not wall:
            obj.hide_render = True
    wall.is_shadow_catcher = False
    scene.render.film_transparent = False
    scene.render.use_motion_blur = False
    scene.frame_set(scene.frame_start)
    scene.render.filepath = str(out / "background.png")
    bpy.ops.render.render(write_still=True)
    print(f"wrote {out / 'background.png'}")


def frame_list(scene, args):
    if args.stills:
        return list(scene["rail_hold_frames"])
    if args.frames:
        frames = []
        for part in args.frames.split(","):
            start, _, end = part.partition("-")
            frames.extend(range(int(start), int(end or start) + 1))
        return frames
    return list(range(scene.frame_start, scene.frame_end + 1))


def write_mp4(out, frames, scene, cfg):
    width = scene.render.resolution_x * scene.render.resolution_percentage // 100
    height = scene.render.resolution_y * scene.render.resolution_percentage // 100
    fps = scene.render.fps
    plate = out / "background.png"
    if plate.exists():
        base = ["-loop", "1", "-framerate", str(fps), "-i", str(plate)]
    else:
        wall = cfg["stage"]["wall_color"].lstrip("#")
        base = ["-f", "lavfi", "-i", f"color=c=0x{wall}:s={width}x{height}:r={fps}"]
    target = out / "preview.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", *base,
            "-framerate", str(fps), "-start_number", str(frames[0]), "-i", str(out / "frame_%04d.png"),
            "-filter_complex", f"[0]scale={width}:{height}[bg];[bg][1]overlay=shortest=1,format=yuv420p",
            "-c:v", "libx264", "-crf", "18", str(target),
        ],
        check=True,
    )
    print(f"wrote {target}")


def main():
    args = parse_args()
    cfg = rail_config.load(args.config)
    scene = bpy.context.scene
    device = enable_gpu(scene)

    if args.preview:
        scene.render.resolution_percentage = cfg["render"]["preview_scale_percent"]
        scene.cycles.samples = cfg["render"]["preview_samples"]
    if args.samples:
        scene.cycles.samples = args.samples
    if args.scale:
        scene.render.resolution_percentage = args.scale
    out = Path(args.out) if args.out else ROOT / "renders" / ("preview" if args.preview else "final")
    out.mkdir(parents=True, exist_ok=True)

    if args.plate:
        render_plate(scene, out)
        return

    frames = frame_list(scene, args)
    animated = [bpy.data.objects[name] for name in scene["rail_animated"]]
    rigs = [bpy.data.objects[name] for name in scene["rail_rigs"]]
    print(f"device {device}, {len(frames)} frames, {scene.cycles.samples} samples, "
          f"{scene.render.resolution_percentage}% of {scene.render.resolution_x}x{scene.render.resolution_y} -> {out}")

    previous_key, previous_path = None, None
    rendered = copied = 0
    started = time.time()
    for i, frame in enumerate(frames):
        path = out / f"frame_{frame:04d}.png"
        key = signature(scene, animated, rigs, frame)
        if args.resume and path.exists():
            pass
        elif key == previous_key and previous_path is not None:
            shutil.copyfile(previous_path, path)
            copied += 1
        else:
            scene.frame_set(frame)
            scene.render.filepath = str(path)
            t0 = time.time()
            bpy.ops.render.render(write_still=True)
            rendered += 1
            elapsed = time.time() - started
            remaining = len(frames) - i - 1
            print(f"frame {frame} in {time.time() - t0:.1f}s ({i + 1}/{len(frames)}, ~{elapsed / (i + 1) * remaining / 60:.0f} min left)", flush=True)
        previous_key, previous_path = key, path

    print(f"done: {rendered} rendered, {copied} copied, {(time.time() - started) / 60:.1f} min")
    if args.mp4 and not args.stills:
        write_mp4(out, frames, scene, cfg)


main()
