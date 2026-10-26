# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow", "scipy"]
# ///
"""Turn the flat-lay hoodie photos into what the Blender scene needs.

Outputs, in build/textures/:
- front_detail.png  fabric shading of the front, color removed (16-bit, 0.5 = neutral)
- back_detail.png   same for the back photo
- logo.png          the chest logo (copied from assets/ or a text placeholder)
- garment.npz       vertex grid snapped to the silhouette, inflation heights, photo metrics

Run: uv run scripts/prepare_textures.py [config/rail.json]
"""

import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "textures"

PLACEHOLDER_FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"


def load_rgb(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float64) / 255.0


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def silhouette(rgb):
    """Separate the garment from a plain studio background.

    The drop shadow is a darker copy of the background color, so it is
    rejected by measuring how far each pixel's chroma strays from the
    background direction rather than its plain RGB distance.
    """
    border = np.concatenate(
        [rgb[:20].reshape(-1, 3), rgb[-20:].reshape(-1, 3), rgb[:, :20].reshape(-1, 3), rgb[:, -20:].reshape(-1, 3)]
    )
    bg = np.median(border, axis=0)
    axis = bg / np.linalg.norm(bg)
    along = rgb @ axis
    ortho = np.linalg.norm(rgb - along[..., None] * axis, axis=-1)
    lum = along / np.linalg.norm(bg)
    mask = (ortho > 0.035) | (lum < 0.55)
    mask = ndi.binary_opening(mask, iterations=2)
    labels, count = ndi.label(mask)
    if count == 0:
        raise SystemExit("No garment found in photo")
    largest = np.argmax(ndi.sum(mask, labels, range(1, count + 1))) + 1
    return ndi.binary_fill_holes(labels == largest)


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return xs.min(), xs.max(), ys.min(), ys.max()


def detail_map(rgb, mask, garment):
    """Fabric shading relative to the garment's own brightness (1.0 = neutral).

    - Near the silhouette the flat-lay photo darkens where the fabric rolls over
      onto the table, and the outermost pixels mix with the background: that
      band is replaced by the nearest pixel `detail_edge_px` inside.
    - Large-scale light from the photo shoot is divided out (Gaussian of
      `detail_flatten_px`), keeping knit, seams, ribs and small folds; the 3D
      lighting provides the rest, and front and back photos then agree.
    - The detail fades to neutral over `detail_rim_fade_px` from the contour,
      where front and back meet on the rim seen head-on in profile.
    """
    lin = srgb_to_linear(rgb)
    lum = lin @ np.array([0.2126, 0.7152, 0.0722])
    core = ndi.binary_erosion(mask, iterations=garment["detail_edge_px"])
    _, (iy, ix) = ndi.distance_transform_edt(~core, return_indices=True)
    lum = lum[iy, ix]
    sigma = garment["detail_flatten_px"]
    weight = mask.astype(np.float64)
    coverage = ndi.gaussian_filter(weight, sigma)
    low = ndi.gaussian_filter(lum * weight, sigma) / np.maximum(coverage, 1e-6)
    # Far outside the garment there is no light to divide by: leave it neutral.
    detail = np.where(coverage > 1e-3, lum / np.maximum(low, 1e-6), 1.0)
    fade = np.clip(ndi.distance_transform_edt(mask) / garment["detail_rim_fade_px"], 0.0, 1.0)
    detail = 1.0 + (detail - 1.0) * fade * fade * (3.0 - 2.0 * fade)
    return np.clip(detail, 0.2, 1.8)


def save_detail(path, detail):
    # Stored halved in 16 bits so 0..2 fits; the shader multiplies by 2.
    data = np.clip(detail * 0.5 * 65535, 0, 65535).astype(np.uint16)
    Image.fromarray(data).save(path)


def placeholder_logo(text, path):
    font = ImageFont.truetype(PLACEHOLDER_FONT, 220)
    left, top, right, bottom = font.getbbox(text)
    pad = 40
    w, h = right - left + 2 * pad, bottom - top + 2 * pad
    img = Image.new("L", (w + 80, h), 0)
    ImageDraw.Draw(img).text((pad - left + 40, pad - top), text, font=font, fill=255)
    # Fake italic: shear to the right like the TKNP wordmark.
    shear = math.tan(math.radians(12))
    img = img.transform(img.size, Image.AFFINE, (1, shear, -shear * h * 0.5, 0, 1, 0), Image.BICUBIC)
    img = img.crop(img.getbbox())
    rgba = Image.new("RGBA", img.size, (255, 255, 255, 0))
    rgba.putalpha(img)
    rgba.save(path)


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config" / "rail.json"
    cfg = json.loads(config_path.read_text())
    garment = cfg["garment"]
    OUT.mkdir(parents=True, exist_ok=True)

    front = load_rgb(ROOT / garment["front"])
    back = load_rgb(ROOT / garment["back"])
    front_mask = silhouette(front)
    back_mask = silhouette(back)
    h_px, w_px = front_mask.shape

    fx0, fx1, fy0, fy1 = bbox(front_mask)
    bx0, bx1, by0, by1 = bbox(back_mask)
    m_per_px = garment["height_m"] / (fy1 - fy0)

    save_detail(OUT / "front_detail.png", detail_map(front, front_mask, garment))
    save_detail(OUT / "back_detail.png", detail_map(back, back_mask, garment))

    # Pillow-shaped inflation: a quarter circle of radius `round_m` from the
    # silhouette, flat at `thickness_m` per side further in. The circle meets
    # the contour vertically, so front and back join in a round rim instead of
    # a ridge (the mesh welds and smooths that rim).
    dist = ndi.distance_transform_edt(front_mask) * m_per_px
    t = np.clip(dist / garment["round_m"], 0.0, 1.0)
    heights = garment["thickness_m"] * np.sqrt(1.0 - (1.0 - t) ** 2)
    heights = ndi.gaussian_filter(heights, 4.0) * front_mask

    # Grid vertices outside the silhouette get pulled onto its nearest pixel,
    # so the mesh border follows the photo contour instead of the grid.
    step = garment["grid_step_px"]
    _, (near_y, near_x) = ndi.distance_transform_edt(~front_mask, return_indices=True)
    snap_px = np.stack([near_x[::step, ::step], near_y[::step, ::step]], axis=-1)
    np.savez_compressed(
        OUT / "garment.npz",
        heights=heights[::step, ::step].astype(np.float32),
        inside=front_mask[::step, ::step],
        snap_px=snap_px.astype(np.float32),
        step_px=step,
        image_size=(w_px, h_px),
        m_per_px=m_per_px,
        front_bbox=(fx0, fx1, fy0, fy1),
        back_bbox=(bx0, bx1, by0, by1),
    )

    logo = garment["logo"]
    logo_src = ROOT / logo["file"]
    if logo_src.exists():
        shutil.copy(logo_src, OUT / "logo.png")
        print(f"logo: {logo_src.relative_to(ROOT)}")
    else:
        placeholder_logo(logo["placeholder_text"], OUT / "logo.png")
        print(f"logo: placeholder '{logo['placeholder_text']}' ({logo['file']} not found)")

    print(f"front bbox x {fx0}-{fx1} y {fy0}-{fy1}, back bbox x {bx0}-{bx1} y {by0}-{by1}")
    print(f"scale {m_per_px * 1000:.3f} mm/px, garment {(fx1 - fx0) * m_per_px:.2f} x {(fy1 - fy0) * m_per_px:.2f} m")
    print(f"wrote {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
