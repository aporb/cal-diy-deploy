#!/usr/bin/env python3
"""Build-time patch: skip Next.js type-checking so cal.diy fits CI RAM limits.

This only relaxes the build step; the pinned cal.diy release already passed
upstream type checks, so runtime behaviour is unchanged.
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "apps/web/next.config.ts")
src = path.read_text()
anchor = '  return {\n    output: process.env.BUILD_STANDALONE === "true" ? "standalone" : undefined,'
replacement = '  return {\n    typescript: { ignoreBuildErrors: true },\n    output: process.env.BUILD_STANDALONE === "true" ? "standalone" : undefined,'
if anchor not in src:
    sys.exit(f"anchor not found in {path}")
path.write_text(src.replace(anchor, replacement, 1))
print(f"Patched {path}: typescript.ignoreBuildErrors = true")
