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

const dayjs = require("dayjs");
dayjs.extend(require("dayjs/plugin/utc"));
dayjs.extend(require("dayjs/plugin/timezone"));
dayjs.extend(require("dayjs/plugin/customParseFormat"));
dayjs.extend(require("dayjs/plugin/localeData"));

const SRC =
  "/tmp/e2e/src/packages/features/ee/workflows/lib/reminders/templates/emailReminderTemplate.ts";

let code = fs.readFileSync(SRC, "utf8");

// redirect the @calcom/* imports to real or minimal equivalents
const subs = [
  [
    'import type { TFunction } from "i18next";',
    "",
  ],
  [
    'import { guessEventLocationType } from "@calcom/app-store/locations";',
    // mirrors cal.com: unknown location value falls back to the raw value
    "const guessEventLocationType = (v) => (v && String(v).includes(\":\") ? undefined : undefined);",
  ],
  ['import dayjs from "@calcom/dayjs";', "const dayjs = globalThis.__dayjs;"],
  [
    'import { APP_NAME } from "@calcom/lib/constants";',
    'const APP_NAME = "HARBOR";',
  ],
  [
    'import { TimeFormat } from "@calcom/lib/timeFormat";',
    'const TimeFormat = { TWELVE_HOUR: "h:mma", TWENTY_FOUR_HOUR: "HH:mm" };',
  ],
  [
    'import { WorkflowActions } from "@calcom/prisma/enums";',
    'const WorkflowActions = { EMAIL_ATTENDEE: "EMAIL_ATTENDEE", EMAIL_ADDRESS: "EMAIL_ADDRESS" };',
  ],
];

for (const [from, to] of subs) {
  if (!code.includes(from)) {
    console.error(`FAIL: could not find import to stub: ${from}`);
    process.exit(1);
  }
  code = code.replace(from, to);
}

if (/@calcom\//.test(code.split("\n").filter((l) => !l.trim().startsWith("*") && !l.trim().startsWith("//")).join("\n"))) {
  console.error("FAIL: an @calcom import survived the stub pass");
  process.exit(1);
}

// strip `export ` so the code can be evaluated inside a function scope
code = code.replace(/^\s*export\s+/gm, "");

const t = ts.transpileModule(code, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});

globalThis.__dayjs = dayjs;
const factory = new Function(
  t.outputText + "; return { emailReminderTemplate, plainTextTemplate, joinLocationParts };"
);
const { emailReminderTemplate, plainTextTemplate, joinLocationParts } = factory();

// a translator that returns the key, so assertions are about structure not copy
const fakeT = (key) => key;

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

console.log("=== the exact production scenario: Google Meet with no link ===");
const broken = emailReminderTemplate({
  isEditingMode: false,
  locale: "en",
  t: fakeT,
  action: "EMAIL_ATTENDEE",
  timeFormat: "h:mma",
  startTime: "2026-10-06T17:15:00.000Z",
  endTime: "2026-10-06T18:00:00.000Z",
  eventName: "Deep Dive",
  timeZone: "America/New_York",
  location: "integrations:google:meet", // what cal.diy actually stored
  meetingUrl: undefined, // <-- the failure condition
  otherPerson: "Issiah Castle",
  name: "Issiah Castle",
  isBrandingDisabled: true,
});

const body = broken.emailBody;
check("rendered without throwing", typeof body === "string" && body.length > 0);
check("does NOT contain the literal word 'undefined'", !body.includes("undefined"), "");
check("does NOT contain 'Google Meet undefined'", !body.includes("Google Meet undefined"));
check("still contains a location line", body.includes(">location: </strong>") || body.includes("location"));

console.log("\n=== with a real meeting link, nothing regresses ===");
const healthy = emailReminderTemplate({
  isEditingMode: false,
  locale: "en",
  t: fakeT,
  action: "EMAIL_ATTENDEE",
  timeFormat: "h:mma",
  startTime: "2026-10-06T17:15:00.000Z",
  endTime: "2026-10-06T18:00:00.000Z",
  eventName: "Deep Dive",
  timeZone: "America/New_York",
  location: "integrations:google:meet",
  meetingUrl: "https://meet.google.com/udu-vsnu-rvp",
  otherPerson: "Issiah Castle",
  name: "Issiah Castle",
  isBrandingDisabled: true,
});
check("link is present when it exists", healthy.emailBody.includes("https://meet.google.com/udu-vsnu-rvp"));
check("no 'undefined' when the link exists", !healthy.emailBody.includes("undefined"));

console.log("\n=== editing-mode preview ===");
const preview = emailReminderTemplate({
  isEditingMode: true,
  locale: "en",
  t: fakeT,
  action: "EMAIL_ATTENDEE",
  timeFormat: "h:mma",
  location: "integrations:google:meet",
  meetingUrl: undefined,
  isBrandingDisabled: false,
});
check("preview html keeps the {LOCATION} token", preview.emailBody.includes("{LOCATION}"));
check("preview does not print a literal undefined", !preview.emailBody.includes("undefined"));

console.log("\n=== joinLocationParts itself ===");
check("drops undefined", joinLocationParts("Google Meet", undefined) === "Google Meet");
check("drops null and empty", joinLocationParts("Google Meet", null, "", "  ") === "Google Meet");
check("joins both when present",
  joinLocationParts("Google Meet", "https://meet.google.com/x") === "Google Meet https://meet.google.com/x");
check("returns empty when nothing is known", joinLocationParts(undefined, undefined) === "");

console.log("\n=== plain-text template string (used by plainTextTemplates.ts) ===");
// This is a static template whose {LOCATION}/{MEETING_URL} tokens are filled by
// customTemplate.ts. It previously asked for BOTH, duplicating the location.
check("plainTextTemplate keeps a single {LOCATION} token",
  plainTextTemplate.includes("Location: {LOCATION}") &&
    !plainTextTemplate.includes("Location: {LOCATION} {MEETING_URL}"));
check("plainTextTemplate no longer asks for {MEETING_URL}",
  !plainTextTemplate.includes("{MEETING_URL}"));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
