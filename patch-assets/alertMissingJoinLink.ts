/**
 * HARBOR PATCH — tell the operator when a booking was confirmed without a join link.
 *
 * Operator decision on 2026-10-01: if the calendar write fails on every retry, still book
 * the client and still email them, but alert the operator so the link can be sent by hand.
 * A confirmation with no join link is only acceptable if the operator knows about it.
 *
 * Deliberately dependency-free and fire-and-forget:
 *  - uses global fetch (Node 20), so the build patch only has to add an import and a call
 *  - never throws, so a broken alert can never cost a client their confirmation
 *
 * Transport: the Resend REST API, authenticated with RESEND_API_KEY when present and
 * otherwise with the EMAIL_SERVER_PASSWORD already in the container. Resend accepts the
 * same key for the API and the SMTP relay, so the alert needs no new environment variable
 * and cannot shadow cal.com's own mailer.
 */

import logger from "@calcom/lib/logger";

const log = logger.getSubLogger({ prefix: ["harbor", "missingJoinLinkAlert"] });

const ALERT_TO = process.env.HARBOR_ALERT_EMAIL || "ap@harborgovcon.com";
const ALERT_FROM = process.env.EMAIL_FROM || "noreply@harborgovcon.com";

export async function alertMissingJoinLink(params: {
  bookingUid: string;
  eventTitle: string;
  attendeeEmails: string[];
  organizerEmail: string;
  startTime?: string;
  calendarError: string;
}): Promise<void> {
  // Prefer an explicit RESEND_API_KEY, but fall back to the SMTP credential that is
  // already in the container. Resend accepts the same key for both the REST API and the
  // SMTP relay, so this means the alert needs NO new environment variable and cannot break
  // cal.com's own mailer by shadowing EMAIL_SERVER_PASSWORD.
  const apiKey = process.env.RESEND_API_KEY || process.env.EMAIL_SERVER_PASSWORD;

  const subject = `Booking confirmed with NO join link — ${params.eventTitle}`;
  const lines = [
    "A booking was confirmed but the calendar write failed on every retry, so no",
    "Google Meet link exists. The client has been emailed without a location line.",
    "",
    `Event:     ${params.eventTitle}`,
    `Starts:    ${params.startTime || "unknown"}`,
    `Booking:   ${params.bookingUid}`,
    `Client:    ${params.attendeeEmails.join(", ") || "unknown"}`,
    `Organizer: ${params.organizerEmail}`,
    "",
    "Calendar error:",
    params.calendarError,
    "",
    "Action: create the meeting and send the client the join link by hand.",
    "Also confirm the event exists on the organizer calendar.",
  ];

  if (!apiKey) {
    // Not fatal: the booking itself is fine, we just cannot reach the operator.
    log.error(
      "Neither RESEND_API_KEY nor EMAIL_SERVER_PASSWORD is set — cannot send the " +
        "missing-join-link alert. The booking was still confirmed. Check the booking manually.",
      JSON.stringify({ bookingUid: params.bookingUid, attendeeEmails: params.attendeeEmails })
    );
    return;
  }

  try {
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: ALERT_FROM,
        to: [ALERT_TO],
        subject,
        text: lines.join("\n"),
      }),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      log.error(
        `Alert failed with HTTP ${response.status}`,
        JSON.stringify({ bookingUid: params.bookingUid, detail: detail.slice(0, 500) })
      );
      return;
    }

    log.info(`Sent missing-join-link alert for booking ${params.bookingUid} to ${ALERT_TO}`);
  } catch (error) {
    log.error(
      "Alert threw — booking is unaffected",
      JSON.stringify({ bookingUid: params.bookingUid, error: String(error) })
    );
  }
}
