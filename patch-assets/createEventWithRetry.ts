/**
 * HARBOR PATCH — retry the calendar event creation on transient failures.
 *
 * Why: on 2026-10-01 three consecutive bookings failed because Google Calendar returned
 * `403 rateLimitExceeded` on `calendar.events.patch`. Each failure happened at a single
 * instant (14:54:53, 15:26:22, 15:35:58) rather than as a sustained outage, and the
 * failure cost the client their calendar event, their Google Meet link and their
 * confirmation email. cal.diy made no attempt to retry, and `gaxios`'s built-in retry
 * does not cover the 403 rate-limit case at all — it retries 429/5xx only.
 *
 * This wrapper is installed by rewriting the single import in EventManager.ts to pull
 * `createEventWithRetry` under the name `createEvent`, so all four existing call sites
 * are covered without touching their bodies.
 *
 * Safety: creating a calendar event is not idempotent, so we retry ONLY when the failure
 * proves Google did not process the request. Anything ambiguous (401, 400, 404, or a 403
 * without a rate-limit reason) is rethrown immediately — retrying a dead credential or a
 * malformed request would just hide a real fault and keep the client waiting.
 */

import { createEvent as createEventOriginal } from "@calcom/features/calendars/lib/CalendarManager";
import logger from "@calcom/lib/logger";
import { safeStringify } from "@calcom/lib/safeStringify";

const log = logger.getSubLogger({ prefix: ["harbor", "calendarRetry"] });

const MAX_ATTEMPTS = 4;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 8000;

const RETRYABLE_STATUSES = new Set([429, 500, 502, 503, 504]);

const RETRYABLE_NETWORK_CODES = new Set([
  "ETIMEDOUT",
  "ECONNRESET",
  "ECONNREFUSED",
  "ENOTFOUND",
  "EAI_AGAIN",
  "EPIPE",
  "ERR_NETWORK",
]);

/** Google's own reason codes for "you are being throttled right now". */
const RETRYABLE_GOOGLE_REASONS = new Set([
  "rateLimitExceeded",
  "userRateLimitExceeded",
  "quotaExceeded",
  "backendError",
  "internalError",
]);

type GoogleishError = {
  code?: number | string;
  status?: number;
  message?: string;
  errors?: Array<{ reason?: string }>;
  response?: {
    status?: number;
    data?: { error?: { errors?: Array<{ reason?: string }>; code?: number } };
  };
};

function collectReasons(error: GoogleishError): string[] {
  const reasons: string[] = [];
  for (const candidate of [
    error.errors,
    error.response?.data?.error?.errors,
  ]) {
    if (Array.isArray(candidate)) {
      for (const entry of candidate) {
        if (entry?.reason) reasons.push(entry.reason);
      }
    }
  }
  return reasons;
}

export function isTransientCalendarError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const e = error as GoogleishError;

  const status = Number(e.code ?? e.status ?? e.response?.status);
  const networkCode = typeof e.code === "string" ? e.code : undefined;
  const reasons = collectReasons(e);

  // Rate-limit reasons win even if the status is unexpected.
  if (reasons.some((reason) => RETRYABLE_GOOGLE_REASONS.has(reason))) return true;

  if (networkCode && RETRYABLE_NETWORK_CODES.has(networkCode)) return true;

  if (Number.isFinite(status) && RETRYABLE_STATUSES.has(status)) return true;

  // A 403 is only retryable when Google said it was a throttle. A bare 403 is a
  // permission problem and must surface immediately.
  return false;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function createEventWithRetry(
  credential: Parameters<typeof createEventOriginal>[0],
  event: Parameters<typeof createEventOriginal>[1],
  externalId?: Parameters<typeof createEventOriginal>[2],
  // Awaited<> is required: the trpc package compiles this file with an ES5 target, where a
  // bare `ReturnType` of an async function is not a valid async return type (TS1055).
): Promise<Awaited<ReturnType<typeof createEventOriginal>>> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await createEventOriginal(credential, event, externalId);
    } catch (error) {
      const transient = isTransientCalendarError(error);

      if (!transient || attempt >= MAX_ATTEMPTS) {
        if (transient) {
          log.error(
            `Calendar create failed after ${attempt} attempts — giving up`,
            safeStringify({ attempt, error })
          );
        }
        throw error;
      }

      const delay = Math.min(BASE_DELAY_MS * 2 ** (attempt - 1), MAX_DELAY_MS);
      log.warn(
        `Transient calendar error, retrying in ${delay}ms (attempt ${attempt} of ${MAX_ATTEMPTS})`,
        safeStringify({ attempt, delay, error })
      );
      await sleep(delay);
    }
  }
}
