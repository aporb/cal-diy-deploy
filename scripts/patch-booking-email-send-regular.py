#!/usr/bin/env python3
"""Build-time patch: send the booking confirmation even when calendar creation fails.

Why this exists (2026-10-02)
----------------------------
A first attempt at this fix (patch-booking-email-send.py) patched `handleConfirmation.ts`,
which turned out to be the WRONG FILE for a normal booking. `handleConfirmation` is only
reached for the request/confirm flow (booking requires confirmation, payment success,
verify-token). A plain booking flows through `RegularBookingService.handleNewBooking`, and
that file carries its own copy of the same bug:

    if (results.length > 0 && results.every((res) => !res.success)) {
      tracingLogger.error(`EventManager.create failure in some of the integrations ...`);
    } else {
      const additionalInformation: AdditionalInformation = {};
      ...
      if (!noEmail) {
        ... await emailsAndSmsHandler.send({ action: BookingActionMap.confirmed, ... });
      }
    }

Observed live 2026-10-02 16:49:58Z: booking `naB3BvXFWebTWV7Ho7nAES` was created and saved as
ACCEPTED (booking id 8, attendee id 13), Google Calendar returned `403 rateLimitExceeded` on
the conference PATCH, this branch was taken, and the confirmation email was never even
attempted -- zero mailer log lines, nothing in the Resend log. The client got no confirmation.

This matches upstream issue #30044 ("All booking emails silently skipped when every calendar
integration result fails"), still open, with unmerged fix PRs #30080 and #30209.

What this changes
-----------------
  - `additionalInformation` is declared before the branch so the email can use it either way
  - the calendar-failure branch still logs, but no longer swallows the email
  - the confirmation email is sent whenever `!noEmail`, regardless of calendar success
  - when the calendar failed (or there is no join link at all), the operator is alerted that
    the client has a confirmation with nothing to click

Ordering note: the email block keeps the exact position it already occupied -- last statement
inside the `isConfirmedByDefault` arm -- so nothing downstream changes. No other code in the
method reads `additionalInformation`.

See operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html and the 2026-10-02 follow-up.
"""
import pathlib
import sys

TARGET = "packages/features/bookings/lib/service/RegularBookingService.ts"
HELPER_SRC = "patch-assets/alertMissingJoinLink.ts"
HELPER_DEST = "packages/features/bookings/lib/harbor/alertMissingJoinLink.ts"

MARKER = "HARBOR PATCH: never swallow the booking confirmation email"

# RegularBookingService lives in lib/service/, so the helper is one level up.
IMPORT_ANCHOR = 'import { handleAppsStatus } from "../handleNewBooking/handleAppsStatus";'

PATCHED_IMPORT = """import { handleAppsStatus } from "../handleNewBooking/handleAppsStatus";
// HARBOR PATCH: alert the operator when a confirmed booking has no join link
import { alertMissingJoinLink } from "../harbor/alertMissingJoinLink";"""

# --- the branch head -------------------------------------------------------
ORIGINAL_HEAD = """    if (results.length > 0 && results.every((res) => !res.success)) {
      const error = {
        errorCode: "BookingCreatingMeetingFailed",
        message: "Booking failed",
      };

      tracingLogger.error(
        `EventManager.create failure in some of the integrations ${organizerUser.username}`,
        safeStringify({ error, results })
      );
    } else {
      const additionalInformation: AdditionalInformation = {};

"""

PATCHED_HEAD = """    // HARBOR PATCH: never swallow the booking confirmation email. Upstream put the
    // confirmation inside the `else`, so one throttled Google Calendar call silenced the
    // invitee's email while the booking was still saved as ACCEPTED.
    const allCalendarEventsFailed = results.length > 0 && results.every((res) => !res.success);
    const additionalInformation: AdditionalInformation = {};
    let calendarErrorText = "";

    if (allCalendarEventsFailed) {
      const error = {
        errorCode: "BookingCreatingMeetingFailed",
        message: "Booking failed",
      };

      calendarErrorText = String(safeStringify({ error, results }));
      tracingLogger.error(
        `EventManager.create failure in some of the integrations ${organizerUser.username}`,
        calendarErrorText
      );
    } else {

"""

# --- the branch tail: email block plus the brace that closes `else` ---------
# Matched as one literal so no brace-walking is needed. The email block is the last
# statement in the branch, so removing this and closing the branch here is exact.
ORIGINAL_TAIL = """      if (!noEmail) {
        if (!isDryRun && !(eventType.seatsPerTimeSlot && rescheduleUid)) {
          await emailsAndSmsHandler.send({
            action: BookingActionMap.confirmed,
            data: {
              eventType: {
                metadata: eventType.metadata,
                schedulingType: eventType.schedulingType,
              },
              eventNameObject,
              workflows,
              evt,
              additionalInformation,
              additionalNotes,
              customInputs,
            },
          });
          bookingEmailsAndSmsTaskerAction = BookingActionMap.confirmed;
        }
      }
    }
"""

PATCHED_TAIL = """    }

    if (!noEmail) {
      if (!isDryRun && !(eventType.seatsPerTimeSlot && rescheduleUid)) {
        await emailsAndSmsHandler.send({
          action: BookingActionMap.confirmed,
          data: {
            eventType: {
              metadata: eventType.metadata,
              schedulingType: eventType.schedulingType,
            },
            eventNameObject,
            workflows,
            evt,
            additionalInformation,
            additionalNotes,
            customInputs,
          },
        });
        bookingEmailsAndSmsTaskerAction = BookingActionMap.confirmed;

        // The confirmation is out. If there is no join link to put in it, the operator has
        // to send one by hand -- so tell them. Deliberately not awaited.
        const joinLink =
          additionalInformation.hangoutLink || evt.videoCallData?.url || videoCallUrl || "";
        if (allCalendarEventsFailed || !joinLink) {
          void alertMissingJoinLink({
            bookingUid: booking.uid,
            eventTitle: evt.title,
            attendeeEmails: (evt.attendees || []).map((attendee) => attendee.email),
            organizerEmail: evt.organizer?.email || "",
            startTime: String(evt.startTime || ""),
            calendarError:
              calendarErrorText ||
              (allCalendarEventsFailed
                ? "calendar creation failed"
                : "calendar succeeded but produced no join link"),
          });
        }
      }
    }
"""


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    path = root / TARGET
    helper_src = root / HELPER_SRC

    if not path.is_file():
        sys.exit(f"target not found: {path}")
    if not helper_src.is_file():
        sys.exit(f"helper asset not found: {helper_src}")

    src = path.read_text()

    if MARKER in src:
        print(f"Already patched, nothing to do: {path}")
        return

    for label, needle in (
        ("import anchor", IMPORT_ANCHOR),
        ("branch head anchor", ORIGINAL_HEAD),
        ("branch tail / email anchor", ORIGINAL_TAIL),
    ):
        count = src.count(needle)
        if count != 1:
            sys.exit(
                f"{label} matched {count} times (expected exactly 1) in {path}.\n"
                "Upstream layout changed -- re-derive the patch against the new source."
            )

    helper_dest = root / HELPER_DEST
    helper_dest.parent.mkdir(parents=True, exist_ok=True)
    helper_dest.write_text(helper_src.read_text())

    src = src.replace(IMPORT_ANCHOR, PATCHED_IMPORT, 1)
    src = src.replace(ORIGINAL_HEAD, PATCHED_HEAD, 1)
    src = src.replace(ORIGINAL_TAIL, PATCHED_TAIL, 1)
    path.write_text(src)

    # ---- verification -------------------------------------------------------
    patched = path.read_text()

    if patched.count("action: BookingActionMap.confirmed,") != 1:
        sys.exit("verification failed: expected exactly one confirmed-booking email call")
    if "alertMissingJoinLink({" not in patched:
        sys.exit("verification failed: operator alert not wired in")
    if patched.count("{") != patched.count("}"):
        sys.exit(
            "verification failed: unbalanced braces after patch "
            f"({patched.count('{')} open vs {patched.count('}')} close)"
        )

    # The email call must no longer sit inside the calendar else-branch. We know the exact
    # text we inserted, so locate that block and confirm the email send comes after the
    # branch closes and before the end of the inserted tail.
    tail_at = patched.index(PATCHED_TAIL)
    branch_close = patched.index("\n\n    if (!noEmail) {", tail_at)
    email_offset = patched.index("action: BookingActionMap.confirmed,")
    if not (tail_at < branch_close < email_offset):
        sys.exit("verification failed: the email call is still inside the calendar branch")

    # And confirm additionalInformation is declared BEFORE the calendar check, not inside it.
    decl_at = patched.index("const additionalInformation: AdditionalInformation = {};")
    check_at = patched.index("if (allCalendarEventsFailed) {")
    if decl_at > check_at:
        sys.exit("verification failed: additionalInformation was not hoisted above the branch")

    print(f"Patched {path}")
    print(f"  installed {HELPER_DEST}")
    print("  - additionalInformation hoisted above the calendar branch")
    print("  - confirmation email now sent regardless of calendar result")
    print("  - operator alerted when the confirmation carries no join link")


if __name__ == "__main__":
    main()
