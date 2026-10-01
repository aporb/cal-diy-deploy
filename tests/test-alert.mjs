/**
 * HARBOR cal.diy patch tests.
 *
 * Usage:  CALDIY_SRC=<patched source root> node tests/<file>.mjs
 * Default source root: /tmp/e2e/src
 *
 * These run the REAL patched files, with @calcom/* imports stubbed and TypeScript
 * stripped by the actual compiler, so they assert behaviour rather than parse success.
 * Requires: npm i typescript dayjs   (see tests/README.md)
 */
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const ts = require("typescript");

const SRC = "/tmp/e2e/src/packages/features/bookings/lib/harbor/alertMissingJoinLink.ts";

let code = fs.readFileSync(SRC, "utf8");

code = code.replace(
  'import logger from "@calcom/lib/logger";',
  `const log = { info: (...a) => globalThis.__logs.push(["info", ...a]),
                warn: (...a) => globalThis.__logs.push(["warn", ...a]),
                error: (...a) => globalThis.__logs.push(["error", ...a]) };`
);
code = code.replace(/\b(?:const|let|var)\s+log\s*=\s*logger\.getSubLogger\([^)]*\);\n/, "");
if (/\blogger\b/.test(code)) {
  console.error("FAIL: a reference to `logger` survived the stub pass");
  process.exit(1);
}
code = code.replace(/^\s*export\s+/gm, "");

const t = ts.transpileModule(code, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});

globalThis.__logs = [];
globalThis.__calls = [];
globalThis.fetch = async (url, opts) => {
  globalThis.__calls.push({ url, opts });
  return globalThis.__fetchImpl(url, opts);
};

const factory = new Function(t.outputText + "; return { alertMissingJoinLink };");
const { alertMissingJoinLink } = factory();

const PARAMS = {
  bookingUid: "4Ze7jEGXgCimMNcyvp8m6W",
  eventTitle: "Deep Dive · 45 min between Amyn Porbanderwala and Issiah Castle",
  attendeeEmails: ["issiah.p.castle1@gmail.com"],
  organizerEmail: "ap@harborgovcon.com",
  startTime: "2026-10-06T17:15:00.000Z",
  calendarError: '{"error":{"errorCode":"BookingCreatingMeetingFailed"}}',
};

let pass = 0,
  fail = 0;
function check(name, condition, detail = "") {
  if (condition) {
    pass++;
    console.log(`[PASS] ${name}${detail ? "  " + detail : ""}`);
  } else {
    fail++;
    console.log(`[FAIL] ${name}${detail ? "  " + detail : ""}`);
  }
}

// --- happy path ----------------------------------------------------------
process.env.RESEND_API_KEY = "re_test_key_not_real";
process.env.HARBOR_ALERT_EMAIL = "ap@harborgovcon.com";
process.env.EMAIL_FROM = "ap@harborgovcon.com";

globalThis.__logs = [];
globalThis.__calls = [];
globalThis.__fetchImpl = async () => ({ ok: true, status: 200, text: async () => "" });

await alertMissingJoinLink(PARAMS);

check("exactly one HTTP call was made", globalThis.__calls.length === 1);
const call = globalThis.__calls[0];
check("posted to the Resend endpoint", call?.url === "https://api.resend.com/emails", call?.url);
check("used the operator key as a bearer token",
  call?.opts?.headers?.Authorization === "Bearer re_test_key_not_real");

const payload = JSON.parse(call?.opts?.body ?? "{}");
check("goes to the configured operator address",
  Array.isArray(payload.to) && payload.to[0] === "ap@harborgovcon.com", JSON.stringify(payload.to));
check("subject names the event", typeof payload.subject === "string" && payload.subject.includes("NO join link"));
check("body names the booking uid", payload.text.includes(PARAMS.bookingUid));
check("body names the client", payload.text.includes("issiah.p.castle1@gmail.com"));
check("body tells the operator what to do", /send the client the join link/i.test(payload.text));
check("logs success", globalThis.__logs.some(([level]) => level === "info"));

// --- falls back to the existing SMTP credential --------------------------
console.log("\n=== RESEND_API_KEY absent, falls back to EMAIL_SERVER_PASSWORD ===");
delete process.env.RESEND_API_KEY;
process.env.EMAIL_SERVER_PASSWORD = "re_smtp_fallback_key";
globalThis.__logs = [];
globalThis.__calls = [];
globalThis.__fetchImpl = async () => ({ ok: true, status: 200, text: async () => "" });
await alertMissingJoinLink(PARAMS);
check("still sends using the SMTP credential", globalThis.__calls.length === 1);
check("bearer uses the fallback credential",
  globalThis.__calls[0]?.opts?.headers?.Authorization === "Bearer re_smtp_fallback_key");

// --- missing key: cannot send, but must not throw ------------------------
console.log("\n=== no credential at all ===");
delete process.env.RESEND_API_KEY;
delete process.env.EMAIL_SERVER_PASSWORD;
globalThis.__logs = [];
globalThis.__calls = [];
let threw = null;
try {
  await alertMissingJoinLink(PARAMS);
} catch (e) {
  threw = e;
}
check("does not throw without a key", threw === null);
check("makes no HTTP call without a key", globalThis.__calls.length === 0);
check("logs an actionable error", globalThis.__logs.some(([level]) => level === "error"));

// --- HTTP failure --------------------------------------------------------
console.log("\n=== Resend returns 500 ===");
process.env.RESEND_API_KEY = "re_test_key_not_real";
globalThis.__logs = [];
globalThis.__fetchImpl = async () => ({ ok: false, status: 500, text: async () => "server error" });
threw = null;
try {
  await alertMissingJoinLink(PARAMS);
} catch (e) {
  threw = e;
}
check("does not throw on HTTP 500", threw === null);
check("logs the failure", globalThis.__logs.some(([level, msg]) => level === "error" && /HTTP 500/.test(String(msg))));

// --- network throw -------------------------------------------------------
console.log("\n=== fetch itself throws ===");
globalThis.__logs = [];
globalThis.__fetchImpl = async () => {
  throw new Error("ENOTFOUND api.resend.com");
};
threw = null;
try {
  await alertMissingJoinLink(PARAMS);
} catch (e) {
  threw = e;
}
check("does not throw when fetch throws", threw === null);
check("logs the thrown error", globalThis.__logs.some(([level]) => level === "error"));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
