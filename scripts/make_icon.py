"""Generate a macOS app icon (1024px) for Pysar, built to Apple's HIG criteria.

Pysar is Ukrainian for *scribe* — the mark is a fountain-pen nib (the scribe's
tool that turns speech into ink). Design follows the macOS icon standards:

  * not full-bleed — a rounded square on ~80% of the canvas, floating on a
    transparent field with a soft drop shadow (the macOS look, vs iOS full-bleed);
  * the rounded square is a true superellipse (squircle, n≈5), not a plain
    rounded rect — continuous curvature like Apple's template;
  * one centred focal element, straight-on (Big Sur perspective), legible at 16px;
  * depth via a subtle gradient + soft outer shadow + a top sheen, no neon, no
    pure black, one warm accent (gold) on a cool slate-ink ground.

Run with the venv active: `python scripts/make_icon.py out.png`.
"""

import math
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024
SS = 3  # supersample for smooth edges
W = S * SS


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(size, top, bot):
    """Vertical RGB gradient image."""
    w, h = size
    g = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(g)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(top, bot, y / h))
    return g


def squircle(cx, cy, a, n=5.0, steps=720):
    """Superellipse (Lamé curve) outline of a rounded square, half-size `a`."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(ct) ** (2.0 / n), ct)
        y = cy + a * math.copysign(abs(st) ** (2.0 / n), st)
        pts.append((x, y))
    return pts


def quad(p0, p1, p2, steps=80):
    """Sample a quadratic bezier p0→p2 with control p1."""
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def nib_outline(cx, nib_top, nib_bot, hw, cap):
    """Outline of a fountain-pen nib: rounded top cap, gently tapered sides
    bowing slightly inward to a fine point."""
    cap_cy = nib_top + cap
    # Upper-half ellipse cap: left shoulder → over the top → right shoulder.
    top = []
    for i in range(121):
        th = math.pi + math.pi * (i / 120)  # 180°..360°
        top.append((cx + hw * math.cos(th), cap_cy + cap * math.sin(th)))
    span = nib_bot - cap_cy
    # Right side down to the point, control pulled slightly inward (concave).
    right = quad((cx + hw, cap_cy), (cx + hw * 0.86, cap_cy + 0.5 * span), (cx, nib_bot))
    # Left side back up to the left shoulder.
    left = quad((cx, nib_bot), (cx - hw * 0.86, cap_cy + 0.5 * span), (cx - hw, cap_cy))
    return top + right[1:] + left[1:]


def main(out_path: str) -> None:
    cx = W // 2

    # ── macOS grid: rounded square on ~80% of the canvas, floating ─────────────
    a = int(0.402 * W)  # half-size → 804/1024 body
    cy_body = int(0.484 * W)  # nudged up to leave room for the shadow below
    body_pts = squircle(cx, cy_body, a, n=5.0)

    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).polygon(body_pts, fill=255)

    canvas = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # Soft outer drop shadow (the macOS floating look).
    sh = mask.copy()
    sh = ImageChops.offset(sh, 0, int(0.018 * W))
    sh = sh.filter(ImageFilter.GaussianBlur(int(0.026 * W)))
    sh = sh.point(lambda v: int(v * 0.42))
    canvas.paste(Image.new("RGBA", (W, W), (8, 10, 14, 255)), (0, 0), sh)

    # ── Ground: cool slate-ink vertical gradient, clipped to the squircle ──────
    top_c = (0x30, 0x37, 0x44)  # slate
    bot_c = (0x15, 0x18, 0x20)  # deep ink, not pure black
    grad = vgrad((W, W), top_c, bot_c)
    body = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    body.paste(grad, (0, 0), mask)

    # Top sheen for glassy depth (subtle, no harsh glow).
    sheen = Image.new("L", (W, W), 0)
    ImageDraw.Draw(sheen).ellipse(
        (cx - a, cy_body - a - int(0.30 * W), cx + a, cy_body - int(0.02 * W)),
        fill=24,
    )
    sheen = sheen.filter(ImageFilter.GaussianBlur(int(0.06 * W)))  # no hard waterline
    sheen = ImageChops.multiply(sheen, mask)
    body.paste(Image.new("RGBA", (W, W), (255, 255, 255, 255)), (0, 0), sheen)
    canvas.alpha_composite(body)

    # ── Nib geometry ───────────────────────────────────────────────────────────
    nib_top = int(cy_body - 0.255 * W)
    nib_bot = int(cy_body + 0.250 * W)
    hw = int(0.140 * W)
    cap = int(0.100 * W)
    pts = nib_outline(cx, nib_top, nib_bot, hw, cap)

    nib_mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(nib_mask).polygon(pts, fill=255)

    # Breather hole + central slit punched out (the unmistakable nib detail).
    cap_cy = nib_top + cap
    span = nib_bot - cap_cy
    bh_y = int(cap_cy + 0.30 * span)
    bh_r = int(0.030 * W)
    holes = Image.new("L", (W, W), 0)
    hd = ImageDraw.Draw(holes)
    hd.ellipse((cx - bh_r, bh_y - bh_r, cx + bh_r, bh_y + bh_r), fill=255)
    sw = int(0.013 * W)
    hd.polygon(
        [
            (cx - sw, bh_y),
            (cx + sw, bh_y),
            (cx + sw // 3, nib_bot - int(0.02 * W)),
            (cx - sw // 3, nib_bot - int(0.02 * W)),
        ],
        fill=255,
    )
    nib_mask = ImageChops.subtract(nib_mask, holes)

    # Nib drop shadow on the ground (depth).
    nsh = nib_mask.copy()
    nsh = ImageChops.offset(nsh, 0, int(0.006 * W))
    nsh = nsh.filter(ImageFilter.GaussianBlur(int(0.009 * W)))
    nsh = nsh.point(lambda v: int(v * 0.40))
    canvas.paste(Image.new("RGBA", (W, W), (0, 0, 0, 255)), (0, 0), nsh)

    # ── Nib body: warm gold metal vertical gradient ────────────────────────────
    gold = vgrad((W, W), (0xF3, 0xD6, 0x88), (0xC3, 0x8C, 0x3C))
    nib = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    nib.paste(gold, (0, 0), nib_mask)

    # Metal sheen: a soft lighter band across the upper nib.
    msheen = Image.new("L", (W, W), 0)
    ImageDraw.Draw(msheen).ellipse(
        (cx - hw, nib_top - int(0.02 * W), cx + hw, cap_cy + int(0.10 * W)),
        fill=70,
    )
    msheen = ImageChops.multiply(msheen, nib_mask)
    nib.paste(Image.new("RGBA", (W, W), (255, 248, 224, 255)), (0, 0), msheen)

    canvas.alpha_composite(nib)

    out = canvas.resize((S, S), Image.LANCZOS)
    out.save(out_path)
    print("wrote", out_path, S, "px")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
