#!/usr/bin/env python3
"""Build-time patch: send the booking confirmation email even when every calendar
integration fails, and alert the operator when that means there is no join link.

Why
---
`handleConfirmation()` in cal.diy v6.2.0 creates the calendar events and then:

    if (results.length > 0 && results.every((res) => !res.success)) {
      tracingLogger.error(`Booking ${user.username} failed`, ...);
    } else {
      ... capture hangoutLink ...
      if (emailsEnabled) { await sendScheduledEmailsAndSMS(...) }
    }

The confirmation email lives inside the `else`, so it is only sent when at least one
calendar integration succeeds. One throttled Google Calendar turns into total email
silence for the invitee and the organizer, while the booking is still persisted as
ACCEPTED -- so the guest believes the meeting exists and never hears from us.

Observed live 2026-10-01: three consecutive bookings took `403 rateLimitExceeded` from
the Google Calendar API. No email was attempted, so nothing was logged as an email
failure and nothing appeared in the Resend logs.

Operator decision: book the client anyway, email them anyway, and alert the operator so
the join link can be sent by hand. A confirmation with no join link is only acceptable if
the operator knows about it.

What this changes
-----------------
  - the calendar-failure branch still logs, but no longer swallows the email
  - event metadata is captured best-effort whenever results carry it
  - the confirmation email is sent whenever `emailsEnabled`
  - when the calendar failed AND there is no join link, `alertMissingJoinLink()` emails
    the operator, fire-and-forget so a broken alert can never cost a confirmation

See operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html (Layers 2 and 4).
"""
import pathlib
import shutil
import sys

TARGET = "packages/features/bookings/lib/handleConfirmation.ts"
HELPER_SRC = "patch-assets/alertMissingJoinLink.ts"
HELPER_DEST = "packages/features/bookings/lib/harbor/alertMissingJoinLink.ts"

MARKER = "HARBOR PATCH: never swallow the confirmation email"

IMPORT_ANCHOR = 'import { sendScheduledEmailsAndSMS } from "@calcom/emails/email-manager";'

PATCHED_IMPORT = """import { sendScheduledEmailsAndSMS } from "@calcom/emails/email-manager";
// HARBOR PATCH: alert the operator when a confirmed booking has no join link
import { alertMissingJoinLink } from "./harbor/alertMissingJoinLink";"""

ORIGINAL = """  if (results.length > 0 && results.every((res) => !res.success)) {
    const error = {
      errorCode: "BookingCreatingMeetingFailed",
      message: "Booking failed",
    };

    tracingLogger.error(`Booking ${user.username} failed`, safeStringify({ error, results }));
  } else {
    if (results.length) {
      // TODO: Handle created event metadata more elegantly
      metadata.hangoutLink = results[0].createdEvent?.hangoutLink;
      metadata.conferenceData = results[0].createdEvent?.conferenceData;
      metadata.entryPoints = results[0].createdEvent?.entryPoints;
    }
    try {
      let isHostConfirmationEmailsDisabled = false;
      let isAttendeeConfirmationEmailDisabled = false;

      if (workflows) {
        isHostConfirmationEmailsDisabled =
          eventTypeMetadata?.disableStandardEmails?.confirmation?.host || false;
        isAttendeeConfirmationEmailDisabled =
          eventTypeMetadata?.disableStandardEmails?.confirmation?.attendee || false;

        if (isHostConfirmationEmailsDisabled) {
          isHostConfirmationEmailsDisabled = allowDisablingHostConfirmationEmails(workflows);
        }

        if (isAttendeeConfirmationEmailDisabled) {
          isAttendeeConfirmationEmailDisabled = allowDisablingAttendeeConfirmationEmails(workflows);
        }
      }

      if (emailsEnabled) {
        await sendScheduledEmailsAndSMS(
          { ...evt, additionalInformation: metadata },
          undefined,
          isHostConfirmationEmailsDisabled,
          isAttendeeConfirmationEmailDisabled,
          eventTypeMetadata
        );
      }
    } catch (error) {
      tracingLogger.error(error);
    }
  }"""

PATCHED = """  // HARBOR PATCH: never swallow the confirmation email. Upstream gated the send behind
  // `else`, so one throttled Google Calendar call silenced the invitee's confirmation
  // while the booking was still saved as ACCEPTED.
  const allCalendarEventsFailed =
    results.length > 0 && results.every((res) => !res.success);
  let calendarErrorText = "";

  if (allCalendarEventsFailed) {
    const error = {
      errorCode: "BookingCreatingMeetingFailed",
      message: "Booking failed",
    };

    // safeStringify returns `unknown` by design (its catch branch returns the raw object),
    // so coerce explicitly rather than assume a string.
    calendarErrorText = String(safeStringify({ error, results }));
    tracingLogger.error(`Booking ${user.username} failed`, calendarErrorText);
  }

  // Best-effort: capture whatever event metadata the results carry.
  if (results.length) {
    // TODO: Handle created event metadata more elegantly
    metadata.hangoutLink = results[0].createdEvent?.hangoutLink;
    metadata.conferenceData = results[0].createdEvent?.conferenceData;
    metadata.entryPoints = results[0].createdEvent?.entryPoints;
  }

  const joinLink = metadata.hangoutLink || evt.videoCallData?.url || "";

  try {
    let isHostConfirmationEmailsDisabled = false;
    let isAttendeeConfirmationEmailDisabled = false;

    if (workflows) {
      isHostConfirmationEmailsDisabled =
        eventTypeMetadata?.disableStandardEmails?.confirmation?.host || false;
      isAttendeeConfirmationEmailDisabled =
        eventTypeMetadata?.disableStandardEmails?.confirmation?.attendee || false;

      if (isHostConfirmationEmailsDisabled) {
        isHostConfirmationEmailsDisabled = allowDisablingHostConfirmationEmails(workflows);
      }

      if (isAttendeeConfirmationEmailDisabled) {
        isAttendeeConfirmationEmailDisabled = allowDisablingAttendeeConfirmationEmails(workflows);
      }
    }

    if (emailsEnabled) {
      await sendScheduledEmailsAndSMS(
        { ...evt, additionalInformation: metadata },
        undefined,
        isHostConfirmationEmailsDisabled,
        isAttendeeConfirmationEmailDisabled,
        eventTypeMetadata
      );

      // The confirmation is out. If there is no join link to put in it, the operator has
      // to send one by hand -- so tell them. Deliberately not awaited.
      if (allCalendarEventsFailed || !joinLink) {
        void alertMissingJoinLink({
          bookingUid: booking.uid,
          eventTitle: evt.title,
          attendeeEmails: (evt.attendees || []).map((attendee) => attendee.email),
          // EventManagerUser has no `email` field -- the CalendarEvent organizer does.
          organizerEmail: evt.organizer?.email || "",
          startTime: evt.startTime,
          calendarError: calendarErrorText || "no join link was produced",
        });
      }
    }
  } catch (error) {
    tracingLogger.error(error);
  }"""


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

    if src.count(ORIGINAL) != 1:
        sys.exit(
            f"anchor matched {src.count(ORIGINAL)} times (expected exactly 1) in {path}.\n"
            "Upstream layout changed -- re-derive the patch against the new source."
        )
    if src.count(IMPORT_ANCHOR) != 1:
        sys.exit(
            f"import anchor matched {src.count(IMPORT_ANCHOR)} times (expected exactly 1) in {path}."
        )

    helper_dest = root / HELPER_DEST
    helper_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(helper_src, helper_dest)

    src = src.replace(IMPORT_ANCHOR, PATCHED_IMPORT, 1)
    src = src.replace(ORIGINAL, PATCHED, 1)
    path.write_text(src)

    # Verify the email call is no longer nested inside an else branch.
    patched = path.read_text()
    lines = patched.splitlines()
    email_line = next(
        (i for i, line in enumerate(lines) if "await sendScheduledEmailsAndSMS(" in line), None
    )
    if email_line is None:
        sys.exit("patch verification failed: sendScheduledEmailsAndSMS call not found")

    indent = len(lines[email_line]) - len(lines[email_line].lstrip())
    enclosing_else = [
        line
        for line in lines[:email_line]
        if line.strip().startswith("} else {") and (len(line) - len(line.lstrip())) < indent
    ]
    if enclosing_else:
        sys.exit("patch verification failed: the email call is still nested inside an else branch")

    if "alertMissingJoinLink({" not in patched:
        sys.exit("patch verification failed: alert call not wired in")

    print(f"Patched {path}")
    print(f"  installed {HELPER_DEST}")
    print("  - calendar-failure log preserved")
    print("  - confirmation email now sent regardless of calendar integration result")
    print("  - operator alerted when a confirmed booking has no join link")


if __name__ == "__main__":
    main()
