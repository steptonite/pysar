"""Generate a clean macOS app icon (1024px) for Pysar.

A warm 'cream' gradient squircle with a simple white microphone glyph.
Run with the venv active: `python scripts/make_icon.py out.png`.
"""

import math
import sys

from PIL import Image, ImageDraw

S = 1024
SS = 4  # supersample for smooth edges
W = S * SS


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main(out_path: str) -> None:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── Squircle background with a vertical warm gradient ──────────────────────
    top = (0xF6, 0xC9, 0x7A)  # soft amber-cream
    bot = (0xE0, 0x9B, 0x4F)  # deeper caramel
    radius = int(0.2237 * W)  # Big Sur-ish corner
    inset = int(0.10 * W)  # content safe margin
    box = (inset, inset, W - inset, W - inset)

    # Paint gradient into a rounded-rect mask.
    grad = Image.new("RGB", (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        gd.line([(0, y), (W, y)], fill=lerp(top, bot, y / W))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # Subtle top sheen for a bit of depth (no harsh glow).
    sheen = Image.new("L", (W, W), 0)
    sd = ImageDraw.Draw(sheen)
    sd.rounded_rectangle((inset, inset, W - inset, inset + int(0.42 * W)),
                         radius=radius, fill=40)
    white = Image.new("RGBA", (W, W), (255, 255, 255, 255))
    img.paste(white, (0, 0), Image.composite(sheen, Image.new("L", (W, W), 0), mask))

    # ── Microphone glyph (white) ───────────────────────────────────────────────
    cx = W // 2
    ink = (255, 255, 255, 255)

    # Capsule body.
    body_w = int(0.205 * W)
    body_top = int(0.300 * W)
    body_bot = int(0.560 * W)
    d.rounded_rectangle(
        (cx - body_w // 2, body_top, cx + body_w // 2, body_bot),
        radius=body_w // 2, fill=ink,
    )

    # U-shaped pickup arc (cradle) around the lower body.
    arc_r = int(0.165 * W)
    arc_top = int(0.420 * W)
    arc_w = int(0.052 * W)
    d.arc(
        (cx - arc_r, arc_top - arc_r, cx + arc_r, arc_top + arc_r),
        start=10, end=170, fill=ink, width=arc_w,
    )

    # Stand stem.
    stem_top = arc_top + arc_r - arc_w // 2
    stem_bot = int(0.700 * W)
    d.rounded_rectangle(
        (cx - arc_w // 2, stem_top, cx + arc_w // 2, stem_bot),
        radius=arc_w // 2, fill=ink,
    )

    # Base.
    base_w = int(0.230 * W)
    base_h = int(0.050 * W)
    d.rounded_rectangle(
        (cx - base_w // 2, stem_bot, cx + base_w // 2, stem_bot + base_h),
        radius=base_h // 2, fill=ink,
    )

    img = img.resize((S, S), Image.LANCZOS)
    img.save(out_path)
    print("wrote", out_path, math.floor(S), "px")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
