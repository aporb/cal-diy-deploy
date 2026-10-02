#!/usr/bin/env python3
"""Build-time patch: retry transient Google Calendar write failures at the HTTP layer.

Why (2026-10-02)
----------------
`CalendarAuth.getClient()` builds the Calendar client with no `retryConfig`, so gaxios
defaults apply: `httpMethodsToRetry` is GET/HEAD/PUT/OPTIONS/DELETE (PATCH and POST are
excluded) and `statusCodesToRetry` is 1xx/429/5xx (403 is excluded). The call that actually
fails in production is:

    calendar.events.patch({ calendarId, eventId, requestBody: { description, location } })

...the write-back that replaces the placeholder location "Google" with the real Meet link.
It is a PATCH, and Google answers a throttled write with 403 `rateLimitExceeded`, so cal.diy
retries it zero times. Observed live: booking `naB3BvXFWebTWV7Ho7nAES`, 2026-10-02
16:49:58Z, one 403, one attempt, no retry. Google's own guidance is the opposite:
"rateLimitExceeded errors can return either 403 or 429 error codes -- currently they are
functionally similar and should be responded to in the same way, by using exponential
backoff."

This matches upstream issue #28834 (open, six unmerged fix PRs). We adopt the shape of PR
#28866, which is the safest of them.

Why patch CalendarAuth and NOT EventManager
-------------------------------------------
An earlier attempt at this (patch-calendar-retry.py) wrapped `createEvent` in EventManager,
which retries the WHOLE create call. That is wrong and has been removed: `events.insert` is
not idempotent, so retrying the wrapper re-sends the insert and can create a DUPLICATE
calendar event and a duplicate Meet link. Retrying at the HTTP layer re-sends only the
individual failed request, which is safe.

Why the retry function is written inline
----------------------------------------
gaxios is present twice in the image (4.3.3 at the root, 6.1.1 nested under
google-auth-library) and the request path uses the nested one. Importing `RetryConfig` from
"gaxios" would resolve against the root copy and risk a nominal-type mismatch. Deriving the
parameter type from the `retryConfig` property instead keeps this file import-free and
exactly matching whichever gaxios the Calendar client is typed against.

Safety
------
- POST is deliberately NOT in `httpMethodsToRetry`: a retried insert creates a second event.
- A custom `shouldRetry` REPLACES gaxios's built-in check, and that check is what normally
  enforces `currentRetryAttempt >= retry`. We therefore enforce the attempt cap ourselves,
  otherwise a persistent 403 would retry forever.
- 403 is only retried for Google's throttling reasons. A bare 403 (permission denied,
  forbiddenForNonOrganizer) and `quotaExceeded` (a hard abuse limit) surface immediately.
"""
import pathlib
import re
import sys

TARGET = "packages/app-store/googlecalendar/lib/CalendarAuth.ts"

MARKER = "HARBOR PATCH: retry transient google calendar writes"

IMPORT_ANCHOR = 'import { OAuth2Client, JWT } from "googleapis-common";'

PATCHED_IMPORT = '''import { OAuth2Client, JWT } from "googleapis-common";

// HARBOR PATCH: retry transient google calendar writes.
const HARBOR_CALENDAR_RETRY_CONFIG = {
  retry: 3,
  noResponseRetries: 2,
  // POST is deliberately absent -- retrying an insert would create a second event.
  httpMethodsToRetry: ["GET", "HEAD", "PUT", "OPTIONS", "DELETE", "PATCH"],
  statusCodesToRetry: [
    [100, 199],
    [403, 403],
    [429, 429],
    [500, 599],
  ],
  // Base backoff is 1s, so attempts land roughly 1s / 2s / 4s apart.
  retryDelay: 1000,
  // Typed by inference from `retryConfig` so this file needs no gaxios import. A custom
  // shouldRetry replaces gaxios's own check, and that check is what normally enforces the
  // attempt cap -- so the cap is applied here instead.
  shouldRetry: (error: {
    code?: unknown;
    status?: unknown;
    errors?: Array<{ reason?: string }>;
    response?: { status?: unknown; data?: { error?: { errors?: Array<{ reason?: string }> } } };
    config?: { retryConfig?: { currentRetryAttempt?: number } };
  }) => {
    if ((error?.config?.retryConfig?.currentRetryAttempt ?? 0) >= 3) return false;

    const status = [error?.code, error?.status, error?.response?.status]
      .map(Number)
      .find((value) => Number.isFinite(value));

    const reasons: string[] = [];
    for (const candidate of [error?.errors, error?.response?.data?.error?.errors]) {
      if (Array.isArray(candidate)) {
        for (const entry of candidate) {
          if (entry?.reason) reasons.push(entry.reason);
        }
      }
    }

    if (status === 403) {
      // Only Google's "throttled right now" reasons. A bare 403 is a permission problem and
      // quotaExceeded is a hard abuse limit -- neither should be retried.
      return ["rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"].some(
        (reason) => reasons.includes(reason)
      );
    }

    if (status === 429) return true;
    if (typeof status === "number" && status >= 500) return true;

    // No response at all: a transport-level failure (ETIMEDOUT, ECONNRESET, ...).
    if (!error?.response) return true;

    return false;
  },
};
'''

ORIGINAL_CLIENT = """    return new calendar_v3.Calendar({
      auth: googleAuthClient,
    });"""

PATCHED_CLIENT = """    return new calendar_v3.Calendar({
      auth: googleAuthClient,
      // HARBOR PATCH: retry the individual throttled request rather than the whole booking,
      // so a transient 403 cannot cost the client their Meet link.
      retryConfig: HARBOR_CALENDAR_RETRY_CONFIG,
    });"""


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    path = root / TARGET

    if not path.is_file():
        sys.exit(f"target not found: {path}")

    src = path.read_text()

    if MARKER in src:
        print(f"Already patched, nothing to do: {path}")
        return

    for label, needle in (
        ("import anchor", IMPORT_ANCHOR),
        ("calendar client anchor", ORIGINAL_CLIENT),
    ):
        count = src.count(needle)
        if count != 1:
            sys.exit(
                f"{label} matched {count} times (expected exactly 1) in {path}.\n"
                "Upstream layout changed -- re-derive the patch against the new source."
            )

    src = src.replace(IMPORT_ANCHOR, PATCHED_IMPORT, 1)
    src = src.replace(ORIGINAL_CLIENT, PATCHED_CLIENT, 1)
    path.write_text(src)

    # ---- verification -------------------------------------------------------
    patched = path.read_text()

    if "retryConfig: HARBOR_CALENDAR_RETRY_CONFIG," not in patched:
        sys.exit("verification failed: retryConfig not attached to the Calendar client")
    if "shouldRetry" not in patched:
        sys.exit("verification failed: shouldRetry missing")

    methods = re.search(r"httpMethodsToRetry:\s*\[([^\]]*)\]", patched)
    if not methods:
        sys.exit("verification failed: could not read httpMethodsToRetry")
    methods_body = methods.group(1)
    if '"PATCH"' not in methods_body:
        sys.exit("verification failed: PATCH is not retried")
    if '"POST"' in methods_body:
        sys.exit("verification failed: POST must NOT be retried (would duplicate events)")

    if not re.search(r"currentRetryAttempt \?\? 0\) >= 3", patched):
        sys.exit(
            "verification failed: the attempt cap must be enforced inside shouldRetry, "
            "because a custom shouldRetry replaces gaxios's own cap"
        )
    if "if (!error?.response) return true;" not in patched:
        sys.exit("verification failed: transport-level failures are not retried")
    if patched.count("{") != patched.count("}"):
        sys.exit(
            f"verification failed: unbalanced braces "
            f"({patched.count('{')} open vs {patched.count('}')} close)"
        )

    print(f"Patched {path}")
    print("  - retryConfig attached to the Google Calendar client")
    print("  - PATCH retried, POST never; 403 only for throttling reasons")
    print("  - attempt cap enforced inside shouldRetry (a custom shouldRetry bypasses gaxios's)")


if __name__ == "__main__":
    main()
