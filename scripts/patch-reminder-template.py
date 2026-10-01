#!/usr/bin/env python3
"""Build-time patch: stop reminder emails printing the literal word "undefined".

Defect in cal.diy v6.2.0, emailReminderTemplate.ts line 44:

    let locationString = `${guessEventLocationType(location)?.label || location} ${meetingUrl}`;

There is no guard on `meetingUrl`. When the conference data could not be created it is
`undefined`, and the template interpolates that into the client's email. Caught live on
2026-10-01 in a queued reminder payload:

    Location: ...</strong></div>Google Meet undefined

Three changes:
  1. join only the location parts that exist, via a small exported helper
  2. use that same helper for the editing-mode preview so the operator sees what will ship
  3. plainTextTemplate asked for both {LOCATION} and {MEETING_URL}; the caller already
     passes a composite `location`, so the duplicate placeholder produced the stray
     duplicate token. Collapse it to {LOCATION}.

See operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html (Layer 3).
"""
import pathlib
import sys

TARGET = (
    "packages/features/ee/workflows/lib/reminders/templates/emailReminderTemplate.ts"
)

MARKER = "HARBOR PATCH: never interpolate an absent meeting URL"

ORIGINAL_HELPER = "const emailReminderTemplate = ({"

PATCHED_HELPER = """// HARBOR PATCH: never interpolate an absent meeting URL. cal.diy built this string with
// a bare template literal, so a missing Google Meet link printed the word "undefined"
// into the client's reminder email.
export const joinLocationParts = (...parts: Array<string | undefined | null>): string =>
  parts.filter((part): part is string => Boolean(part && part.trim())).join(" ");

const emailReminderTemplate = ({"""

ORIGINAL_LOCATION_STRING = (
    "let locationString = `${guessEventLocationType(location)?.label || location} ${meetingUrl}`;"
)

PATCHED_LOCATION_STRING = (
    "let locationString = joinLocationParts(\n"
    "    guessEventLocationType(location)?.label || location,\n"
    "    meetingUrl\n"
    "  );"
)

ORIGINAL_EDITING = 'locationString = "{LOCATION} {MEETING_URL}";'
PATCHED_EDITING = 'locationString = "{LOCATION}";'

ORIGINAL_PLAIN = "Attendees: You & {ATTENDEE}Location: {LOCATION} {MEETING_URL}`;"
PATCHED_PLAIN = "Attendees: You & {ATTENDEE}Location: {LOCATION}`;"

REPLACEMENTS = [
    (ORIGINAL_HELPER, PATCHED_HELPER, "helper"),
    (ORIGINAL_LOCATION_STRING, PATCHED_LOCATION_STRING, "live location string"),
    (ORIGINAL_EDITING, PATCHED_EDITING, "editing-mode preview"),
    (ORIGINAL_PLAIN, PATCHED_PLAIN, "plain-text template"),
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
                "Upstream layout changed -- re-derive the patch against the new source."
            )

    for original, replacement, _label in REPLACEMENTS:
        src = src.replace(original, replacement, 1)

    path.write_text(src)

    # Verify the defect is actually gone.
    patched = path.read_text()
    if "joinLocationParts(" not in patched:
        sys.exit("patch verification failed: helper not applied")
    if "{LOCATION} {MEETING_URL}" in patched:
        sys.exit("patch verification failed: double location placeholder still present")
    if "?.label || location} ${meetingUrl}" in patched:
        sys.exit("patch verification failed: unguarded interpolation still present")

    # The helper must be MODULE scoped, declared before the template. Anchoring on a
    # function body line instead would nest it inside the template, where it is not
    # reachable from outside -- caught in testing 2026-10-01.
    helper_at = patched.find("export const joinLocationParts")
    template_at = patched.find("const emailReminderTemplate = ({")
    if helper_at == -1 or template_at == -1 or helper_at > template_at:
        sys.exit(
            "patch verification failed: joinLocationParts must be declared at module scope, "
            "before emailReminderTemplate"
        )

    print(f"Patched {path}")
    print("  - location string now omits absent parts instead of printing 'undefined'")
    print("  - editing-mode preview matches what is sent")
    print("  - plain-text template no longer duplicates the location placeholder")


if __name__ == "__main__":
    main()
