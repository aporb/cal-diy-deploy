#!/usr/bin/env python3
"""Generate the HARBOR app icons that replace Cal.com's stock marks.

Why this exists
---------------
cal.diy serves its favicon from a static fallback file:

  services: getTeamLogos(subdomain, isValidOrgDomain)

`getTeamLogos` lives under packages/features/ee and reads team/organization logo records.
This deployment has ORGANIZATIONS_ENABLED=false and no team, so nothing is ever returned and
every icon request falls through to the default files in apps/web/public/:

    favicon-16x16.png  favicon-32x32.png  apple-touch-icon.png  favicon.ico
    cal-com-icon-white.svg   calcom-logo-white-word.svg

Rebranding by uploading art in settings is therefore not available here (and would be an
ee feature anyway). Replacing those files at build time is licence-free, deterministic, and
has no dependency on any database state.

Design
------
The HARBOR mark, matching projects/harbor-website/src/app/icon.svg: a filled circle in the
brand blue #3B82F6 with a white "H". The letterform is DRAWN from rectangles rather than set
in a font, so output cannot drift with whatever fonts a build machine happens to have.

Rasters are drawn at 8x and downsampled with LANCZOS, so the 16px favicon is properly
antialiased rather than nearest-neighbour mush.

Usage:  python3 assets/generate-harbor-icons.py <output-dir>
"""
import pathlib
import sys

from PIL import Image, ImageDraw

# Brand blue, from projects/harbor-website/src/app/icon.svg
BRAND_BLUE = (59, 130, 246, 255)
WHITE = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)

SUPERSAMPLE = 8

# The brand's sequential palette, used for the wordmark lettering.
PALETTE = ["#3B82F6", "#8B5CF6", "#EC4899", "#F97316", "#10B981", "#06B6D4"]
WORDMARK = "HARBOR"


def hex_rgba(value: str, alpha: int = 255) -> tuple:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def draw_icon(size: int) -> Image.Image:
    """A circle with a white H, drawn on a transparent field."""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), TRANSPARENT)
    d = ImageDraw.Draw(img)

    # circle, full bleed
    d.ellipse((0, 0, big - 1, big - 1), fill=BRAND_BLUE)

    # H geometry, in units of a 60-unit viewBox scaled to the canvas
    u = big / 60.0

    def rect(x, y, w, h):
        d.rectangle((x * u, y * u, (x + w) * u - 1, (y + h) * u - 1), fill=WHITE)

    bar_w = 6.0
    left_x, right_x = 17.5, 36.5
    top_y, bot_y = 18.5, 41.5
    cross_h = 5.0

    rect(left_x, top_y, bar_w, bot_y - top_y)                       # left stem
    rect(right_x, top_y, bar_w, bot_y - top_y)                      # right stem
    rect(left_x, 30.0 - cross_h / 2, right_x - left_x + bar_w, cross_h)  # crossbar

    return img.resize((size, size), Image.LANCZOS)


def icon_svg(size: int = 60) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60" '
        f'width="{size}" height="{size}" role="img" aria-label="HARBOR">\n'
        f'  <title>HARBOR</title>\n'
        f'  <circle cx="30" cy="30" r="30" fill="#3B82F6"/>\n'
        f'  <g fill="#FFFFFF">\n'
        f'    <rect x="17.5" y="18.5" width="6" height="23"/>\n'
        f'    <rect x="36.5" y="18.5" width="6" height="23"/>\n'
        f'    <rect x="17.5" y="27.5" width="25" height="5"/>\n'
        f'  </g>\n'
        f'</svg>\n'
    )


def wordmark_svg(width: int = 320, height: int = 56, letter_px: int = 20) -> str:
    """HARBOR spelled as the brand's coloured letter circles."""
    r = (height - 16) / 2
    cy = height / 2
    gap = 38
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="HARBOR">',
        "  <title>HARBOR</title>",
    ]
    for i, letter in enumerate(WORDMARK):
        cx = 20 + i * gap
        parts.append(f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{PALETTE[i % len(PALETTE)]}"/>')
        parts.append(
            f'  <text x="{cx}" y="{cy + letter_px * 0.35}" font-family="system-ui, '
            f'-apple-system, sans-serif" font-size="{letter_px}" font-weight="800" '
            f'fill="#FFFFFF" text-anchor="middle">{letter}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)

    # PNG sizes cal.diy asks for, plus the sizes its /api/logo route resizes to.
    pngs = {
        "favicon-16x16.png": 16,
        "favicon-32x32.png": 32,
        "favicon-48x48.png": 48,
        "apple-touch-icon.png": 180,
        "android-chrome-192x192.png": 192,
        "android-chrome-256x256.png": 256,
        "mstile-150x150.png": 150,
    }
    for name, size in pngs.items():
        draw_icon(size).save(out / name, "PNG", optimize=True)
        print(f"  {name:32s} {size}x{size}")

    # multi-resolution .ico: 16/32/48 is what browsers actually pick from
    ico_sizes = [16, 32, 48]
    base = draw_icon(48)
    base.save(out / "favicon.ico", format="ICO", sizes=[(s, s) for s in ico_sizes])
    print(f"  {'favicon.ico':32s} {ico_sizes}")

    # SVGs: the icon, and the wordmark for the in-app header
    (out / "cal-com-icon-white.svg").write_text(icon_svg())
    print(f"  {'cal-com-icon-white.svg':32s} (icon)")
    (out / "calcom-logo-white-word.svg").write_text(wordmark_svg())
    print(f"  {'calcom-logo-white-word.svg':32s} (wordmark)")

    # a plainly named copy of the icon for future re-use
    (out / "harbor-icon.svg").write_text(icon_svg())
    print(f"  {'harbor-icon.svg':32s} (reference)")

    print(f"\nGenerated into {out}")


if __name__ == "__main__":
    main()
