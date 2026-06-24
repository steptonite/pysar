"""Generate a clean macOS app icon (1024px) for Pysar.

Pysar is Ukrainian for *scribe* — so the mark is a calligraphy pen nib (the
scribe's tool that turns speech into ink), not the generic dictation microphone.
A parchment-toned nib sits on a deep slate-ink squircle with a single warm
breather-hole accent. Restrained on purpose: no glow, no pure black, one accent.

Run with the venv active: `python scripts/make_icon.py out.png`.
"""

import math
import sys

from PIL import Image, ImageChops, ImageDraw

S = 1024
SS = 4  # supersample for smooth edges
W = S * SS


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main(out_path: str) -> None:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # ── Squircle background: deep slate-ink vertical gradient ──────────────────
    top = (0x33, 0x39, 0x45)  # slate, not pure black
    bot = (0x1B, 0x1F, 0x27)  # deeper ink
    radius = int(0.2237 * W)  # Big Sur-ish corner
    inset = int(0.10 * W)  # content safe margin
    box = (inset, inset, W - inset, W - inset)

    grad = Image.new("RGB", (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        gd.line([(0, y), (W, y)], fill=lerp(top, bot, y / W))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # Subtle top sheen for depth (no harsh glow).
    sheen = Image.new("L", (W, W), 0)
    ImageDraw.Draw(sheen).rounded_rectangle(
        (inset, inset, W - inset, inset + int(0.42 * W)), radius=radius, fill=26
    )
    white = Image.new("RGBA", (W, W), (255, 255, 255, 255))
    img.paste(white, (0, 0), Image.composite(sheen, Image.new("L", (W, W), 0), mask))

    # ── Pen-nib glyph (parchment) ──────────────────────────────────────────────
    cx = W // 2
    ink = (0xF4, 0xEF, 0xE3, 255)  # warm parchment

    nib_top = int(0.300 * W)
    nib_bot = int(0.748 * W)  # the writing point
    nib_half = int(0.170 * W)  # half width at the shoulders
    sr = int(0.085 * W)  # shoulder corner radius
    span = nib_bot - nib_top

    nib = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    nd = ImageDraw.Draw(nib)
    # Rounded top band → gives the nib its rounded shoulders.
    nd.rounded_rectangle((cx - nib_half, nib_top, cx + nib_half, nib_top + 2 * sr),
                         radius=sr, fill=ink)
    # Body tapering to a sharp point.
    nd.polygon([(cx - nib_half, nib_top + sr), (cx + nib_half, nib_top + sr),
                (cx, nib_bot)], fill=ink)

    # Breather hole + central slit, punched out so the slate shows through and the
    # nib reads as two tines (the unmistakable fountain-pen silhouette).
    bh_y = nib_top + int(0.36 * span)
    bh_r = int(0.034 * W)
    slit_w = int(0.016 * W)
    holes = Image.new("L", (W, W), 0)
    hd = ImageDraw.Draw(holes)
    hd.ellipse((cx - bh_r, bh_y - bh_r, cx + bh_r, bh_y + bh_r), fill=255)
    hd.rounded_rectangle((cx - slit_w // 2, bh_y, cx + slit_w // 2, nib_bot - int(0.015 * W)),
                         radius=slit_w // 2, fill=255)
    na = ImageChops.subtract(nib.split()[3], holes)
    nib.putalpha(na)
    img.alpha_composite(nib)

    # Single warm accent: refill the breather hole with a muted ochre dot.
    ImageDraw.Draw(img).ellipse(
        (cx - bh_r, bh_y - bh_r, cx + bh_r, bh_y + bh_r), fill=(0xD6, 0x9C, 0x57, 255)
    )

    img = img.resize((S, S), Image.LANCZOS)
    img.save(out_path)
    print("wrote", out_path, math.floor(S), "px")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
