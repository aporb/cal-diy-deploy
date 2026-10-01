#!/usr/bin/env python3
"""Build-time patch: route calendar event creation through a retrying wrapper.

Installs `patch-assets/createEventWithRetry.ts` into the bookings lib and rewrites the
single import in EventManager.ts so `createEvent` resolves to the retrying version. All
four existing call sites keep their bodies untouched, which keeps the diff to one line and
means upstream refactors of those call sites do not break the patch.

Background: 2026-10-01, three bookings in a row lost their calendar event, Google Meet link
and confirmation email because Google returned `403 rateLimitExceeded` at a single instant
each time. See operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html (Layer 1).
"""
import pathlib
import shutil
import sys

EVENT_MANAGER = "packages/features/bookings/lib/EventManager.ts"
HELPER_SRC = "patch-assets/createEventWithRetry.ts"
HELPER_DEST = "packages/features/bookings/lib/harbor/createEventWithRetry.ts"

ORIGINAL_IMPORT = (
    'import { createEvent, updateEvent, deleteEvent } from '
    '"@calcom/features/calendars/lib/CalendarManager";'
)

PATCHED_IMPORT = (
    "// HARBOR PATCH: createEvent now retries transient calendar failures. See harbor/createEventWithRetry.ts\n"
    'import { updateEvent, deleteEvent } from "@calcom/features/calendars/lib/CalendarManager";\n'
    'import { createEventWithRetry as createEvent } from "./harbor/createEventWithRetry";'
)

MARKER = "HARBOR PATCH: createEvent now retries transient calendar failures"


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    manager = root / EVENT_MANAGER
    helper_src = root / HELPER_SRC

    if not manager.is_file():
        sys.exit(f"target not found: {manager}")
    if not helper_src.is_file():
        sys.exit(f"helper asset not found: {helper_src}")

    src = manager.read_text()

    if MARKER in src:
        print(f"Already patched, nothing to do: {manager}")
        return

    if src.count(ORIGINAL_IMPORT) != 1:
        sys.exit(
            f"import anchor matched {src.count(ORIGINAL_IMPORT)} times (expected exactly 1) "
            f"in {manager}.\nUpstream layout changed -- re-derive the patch against the new source."
        )

    # Install the helper alongside the code that uses it.
    helper_dest = root / HELPER_DEST
    helper_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(helper_src, helper_dest)

    manager.write_text(src.replace(ORIGINAL_IMPORT, PATCHED_IMPORT, 1))

    # Verify: createEvent must now come from our wrapper, and must still be called.
    patched = manager.read_text()
    if 'from "./harbor/createEventWithRetry"' not in patched:
        sys.exit("patch verification failed: retrying import not present")
    if "createEvent(" not in patched:
        sys.exit("patch verification failed: no createEvent call sites remain")

    print(f"Patched {manager}")
    print(f"  installed {HELPER_DEST}")
    print("  - createEvent call sites now retry transient failures (4 attempts, 1/2/4s)")


if __name__ == "__main__":
    main()
