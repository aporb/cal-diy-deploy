#!/usr/bin/env python3
"""Build-time patch: let the build set HARBOR branding.

cal.diy's Dockerfile enumerates every build arg explicitly, so a new one cannot simply be
passed from the workflow -- the Dockerfile has to declare it first. This adds:

    NEXT_PUBLIC_APP_NAME   what the browser tab and other APP_NAME-derived labels say
    EMAIL_FROM_NAME        the display name on outgoing mail

APP_NAME is `process.env.NEXT_PUBLIC_APP_NAME || "Cal.com"` in packages/lib/constants.ts,
and the booking page's <title> is built as `| ${APP_NAME}`. Without this the public page
title reads "Amyn Porbanderwala | Cal.com".

Both are set in the builder stage (NEXT_PUBLIC_* values are inlined when the client bundle
is compiled) and in the runner stage (so the server-side value resolves at runtime too and
the two can never disagree).

See operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html (Layer 5).
"""
import pathlib
import re
import sys

TARGET = "Dockerfile"

MARKER = "HARBOR PATCH: branding build args"

# builder stage: declare the args and thread them into the existing ENV block, in one
# replacement so the two edits cannot interleave badly.
ORIGINAL_BUILDER = """ARG NEXT_PUBLIC_SINGLE_ORG_SLUG
ARG ORGANIZATIONS_ENABLED

ENV NEXT_PUBLIC_WEBAPP_URL=http://NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER \\"""

PATCHED_BUILDER = """ARG NEXT_PUBLIC_SINGLE_ORG_SLUG
ARG ORGANIZATIONS_ENABLED
# HARBOR PATCH: branding build args
ARG NEXT_PUBLIC_APP_NAME=HARBOR
ARG EMAIL_FROM_NAME=HARBOR

ENV NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \\
  EMAIL_FROM_NAME=$EMAIL_FROM_NAME \\
  NEXT_PUBLIC_WEBAPP_URL=http://NEXT_PUBLIC_WEBAPP_URL_PLACEHOLDER \\"""

# runner stage: make the same values available at runtime
ORIGINAL_RUNNER = """COPY --from=builder-two /calcom ./
ARG NEXT_PUBLIC_WEBAPP_URL=http://localhost:3000
ENV NEXT_PUBLIC_WEBAPP_URL=$NEXT_PUBLIC_WEBAPP_URL \\
  BUILT_NEXT_PUBLIC_WEBAPP_URL=$NEXT_PUBLIC_WEBAPP_URL"""

PATCHED_RUNNER = """COPY --from=builder-two /calcom ./
ARG NEXT_PUBLIC_WEBAPP_URL=http://localhost:3000
ARG NEXT_PUBLIC_APP_NAME=HARBOR
ARG EMAIL_FROM_NAME=HARBOR
ENV NEXT_PUBLIC_WEBAPP_URL=$NEXT_PUBLIC_WEBAPP_URL \\
  BUILT_NEXT_PUBLIC_WEBAPP_URL=$NEXT_PUBLIC_WEBAPP_URL \\
  NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \\
  EMAIL_FROM_NAME=$EMAIL_FROM_NAME"""

REPLACEMENTS = [
    (ORIGINAL_BUILDER, PATCHED_BUILDER, "builder stage"),
    (ORIGINAL_RUNNER, PATCHED_RUNNER, "runner stage"),
]


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    path = root / TARGET

    if not path.is_file():
        sys.exit(f"target not found: {path}")

    src = path.read_text()

    if MARKER in src:
        print(f"Already patched, nothing to do: {path}")
        return

    for original, _replacement, label in REPLACEMENTS:
        count = src.count(original)
        if count != 1:
            sys.exit(
                f"anchor for {label} matched {count} times (expected exactly 1) in {path}.\n"
                "Upstream Dockerfile changed -- re-derive the patch against the new source."
            )

    for original, replacement, _label in REPLACEMENTS:
        src = src.replace(original, replacement, 1)

    path.write_text(src)

    patched = path.read_text()

    # Count DECLARATIONS, not substring hits: `EMAIL_FROM_NAME=$NEXT_PUBLIC_APP_NAME` is a
    # value reference to the other variable, not a second declaration of it. A declaration
    # is either a bare `ARG NAME` or an `ENV NAME=...` key at the start of a continued line.
    def declarations(text: str, name: str) -> int:
        # A declaration is an ENV key. Multi-line ENV blocks put the first key on the
        # `ENV NAME=...` line and the rest on bare continuation lines, so strip a leading
        # ENV before comparing. `ARG NAME=default` is intentionally not counted here; a
        # separate check below asserts those landed.
        found = 0
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("ENV "):
                stripped = stripped[4:].lstrip()
            if stripped == name or stripped.startswith(name + "="):
                found += 1
        return found

    # An `ARG NAME=default` line does not start with "NAME=", so it is not counted here.
    # What matters is that each variable is assigned in the builder ENV block and again in
    # the runner ENV block -- two declarations each.
    for var in ("NEXT_PUBLIC_APP_NAME", "EMAIL_FROM_NAME"):
        declared = declarations(patched, var)
        if declared != 2:
            sys.exit(
                f"patch verification failed: expected 2 {var} ENV declarations "
                f"(builder + runner), found {declared}"
            )

    for var in ("NEXT_PUBLIC_APP_NAME", "EMAIL_FROM_NAME"):
        arg_defaults = patched.count(f"ARG {var}=")
        if arg_defaults != 2:
            sys.exit(
                f"patch verification failed: expected 2 'ARG {var}=' lines "
                f"(builder + runner), found {arg_defaults}"
            )

    # Every ENV line in a continued block except the last must keep its trailing backslash
    if "EMAIL_FROM_NAME=$EMAIL_FROM_NAME \\\n" not in patched:
        sys.exit("patch verification failed: EMAIL_FROM_NAME ENV line is not continued")
    # The builder ENV block ends with CSP_POLICY; if the injected keys broke the block's
    # continuation, that line would be mangled or gone.
    if "CSP_POLICY=$CSP_POLICY" not in patched:
        sys.exit("patch verification failed: the builder ENV block looks truncated")

    print(f"Patched {path}")
    print("  - builder stage: NEXT_PUBLIC_APP_NAME + EMAIL_FROM_NAME as ARG and ENV")
    print("  - runner stage: same two available at runtime")


if __name__ == "__main__":
    main()
