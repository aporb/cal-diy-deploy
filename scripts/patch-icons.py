#!/usr/bin/env python3
"""Build-time patch: replace Cal.com's stock app icons with HARBOR's.

The problem
-----------
cal.diy serves its favicon and app icon from a static fallback:

    apps/web/app/api/logo/route.ts
      const teamLogos = await getTeamLogos(subdomain, isValidOrgDomain);
      const filteredLogo = teamLogos[logoDefinition.source] ?? logoDefinition.fallback;

`getTeamLogos` lives under `packages/features/ee` and reads team/organization logo records.
This deployment sets ORGANIZATIONS_ENABLED=false and has no team, so that lookup yields
nothing and every request falls through to the default files in `apps/web/public/`. Those are
Cal.com's marks -- a browser tab reading "Cal", not HARBOR.

Why not upload art in settings: it is an ee/branding feature and has no records to attach to
here. Replacing the fallback files is licence-free, deterministic, and does not depend on any
database state, so it cannot silently revert.

What this does
--------------
Overwrites exactly the files the fallback constants point at. Each replacement is asserted to
have landed. If upstream renames or removes one, the build fails loudly rather than shipping a
half-rebranded app.

Icons are produced by assets/generate-harbor-icons.py -- regenerate there, not by hand.

See operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html (Layer 5).
"""
import pathlib
import shutil
import sys

ASSET_DIR = "assets/harbor-icons"
PUBLIC_DIR = "apps/web/public"

# Only files the fallback constants actually name, plus the sizes browsers ask for.
# FAVICON_16 / FAVICON_32 / APPLE_TOUCH_ICON / LOGO_ICON / LOGO all live in
# packages/lib/constants.ts.
REPLACEMENTS = {
    "favicon.ico": "favicon.ico",
    "favicon-16x16.png": "favicon-16x16.png",
    "favicon-32x32.png": "favicon-32x32.png",
    "apple-touch-icon.png": "apple-touch-icon.png",
    "cal-com-icon-white.svg": "cal-com-icon-white.svg",  # LOGO_ICON
    "calcom-logo-white-word.svg": "calcom-logo-white-word.svg",  # LOGO
}

MARKER = "HARBOR icon patch marker"


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    assets = root / ASSET_DIR
    public = root / PUBLIC_DIR

    if not assets.is_dir():
        sys.exit(f"asset dir not found: {assets}")
    if not public.is_dir():
        sys.exit(f"public dir not found: {public}")

    # Every destination must already exist. Creating one would mean the constant that points
    # at it changed upstream, which we want to hear about.
    missing = [name for name in REPLACEMENTS if not (public / name).is_file()]
    if missing:
        sys.exit(
            "expected default icon(s) missing from "
            f"{public}: {', '.join(missing)}\n"
            "Upstream moved or renamed them -- re-derive this patch against the new source."
        )

    for src_name, dest_name in REPLACEMENTS.items():
        src = assets / src_name
        if not src.is_file():
            sys.exit(f"asset not found: {src}")
        shutil.copyfile(src, public / dest_name)

    # Also drop the extra sizes and the reference icon in; harmless, and keeps the app's
    # android/mstile endpoints branded too.
    for extra in ("favicon-48x48.png", "android-chrome-192x192.png",
                  "android-chrome-256x256.png", "mstile-150x150.png"):
        src = assets / extra
        if src.is_file():
            shutil.copyfile(src, public / extra)

    # Verify the two that matter most are no longer Cal.com's bytes.
    for name in ("favicon-32x32.png", "cal-com-icon-white.svg"):
        dest = public / name
        asset = assets / name
        if dest.read_bytes() != asset.read_bytes():
            sys.exit(f"patch verification failed: {name} was not replaced")
        if dest.stat().st_size == 0:
            sys.exit(f"patch verification failed: {name} is empty")

    # The stock cal.diy wordmark says "Cal.com"; make sure ours cannot.
    wordmark = (public / "calcom-logo-white-word.svg").read_text(errors="replace")
    if "cal.com" in wordmark.lower():
        sys.exit("patch verification failed: the wordmark still mentions cal.com")

    print(f"Patched app icons in {public}")
    print(f"  replaced {len(REPLACEMENTS)} fallback files with HARBOR marks")
    print("  favicon, apple-touch, android and mstile sizes installed")
    print(f"  {MARKER}")


if __name__ == "__main__":
    main()
