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

const SRC = "/tmp/e2e/src/packages/features/bookings/lib/harbor/createEventWithRetry.ts";

// --- build a runnable copy: strip type-only syntax, redirect @calcom/* to stubs ---
let code = fs.readFileSync(SRC, "utf8");

// 1. replace the three @calcom imports with an injected control handle
// NOTE: must forward to globalThis at CALL time, not capture at load time,
// otherwise the harness cannot swap the implementation per test.
code = code.replace(
  'import { createEvent as createEventOriginal } from "@calcom/features/calendars/lib/CalendarManager";',
  "const createEventOriginal = (...args) => globalThis.__createEventOriginal(...args);"
);
code = code.replace(
  'import logger from "@calcom/lib/logger";',
  `const log = { warn: (...a) => globalThis.__logs.push(["warn", ...a]),
                error: (...a) => globalThis.__logs.push(["error", ...a]),
                info: (...a) => globalThis.__logs.push(["info", ...a]) };`
);
code = code.replace(
  'import { safeStringify } from "@calcom/lib/safeStringify";',
  "const safeStringify = (v) => { try { return JSON.stringify(v); } catch { return String(v); } };"
);
// the original file does `const log = logger.getSubLogger(...)`; drop that line.
// match const/let/var so the stub cannot silently no-op.
const before = code;
code = code.replace(/\b(?:const|let|var)\s+log\s*=\s*logger\.getSubLogger\([^)]*\);\n/, "");
if (code === before) {
  console.error("FAIL: could not remove the getSubLogger line -- stub would silently no-op");
  process.exit(1);
}
if (/\blogger\b/.test(code)) {
  console.error("FAIL: a reference to `logger` survived the stub pass");
  process.exit(1);
}

if (code.includes("@calcom/")) {
  console.error("FAIL: an @calcom import survived the stub pass");
  process.exit(1);
}

// strip the `export ` keywords so we can eval it in a function scope
code = code.replace(/^export /gm, "");

// strip all TypeScript syntax with the real compiler
const transpiled = ts.transpileModule(code, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});
code = transpiled.outputText;

const factory = new Function(
  `${code}
   return { createEventWithRetry, isTransientCalendarError };`
);

globalThis.__logs = [];
globalThis.__calls = 0;
globalThis.__createEventOriginal = async () => {
  throw new Error("test did not install a stub");
};
const { createEventWithRetry, isTransientCalendarError } = factory();

// --- helpers -------------------------------------------------------------
const rateLimit403 = () => {
  const e = new Error("Rate Limit Exceeded");
  e.code = 403;
  e.errors = [{ domain: "usageLimits", reason: "rateLimitExceeded", message: "Rate Limit Exceeded" }];
  return e;
};
const plain403 = () => { const e = new Error("Forbidden"); e.code = 403; return e; };
const auth401 = () => { const e = new Error("Invalid Credentials"); e.code = 401; return e; };
const server503 = () => { const e = new Error("Backend Error"); e.code = 503; return e; };
const networkTimeout = () => { const e = new Error("timeout"); e.code = "ETIMEDOUT"; return e; };

let pass = 0, fail = 0;
function check(name, condition, detail = "") {
  if (condition) { pass++; console.log(`[PASS] ${name}${detail ? "  " + detail : ""}`); }
  else { fail++; console.log(`[FAIL] ${name}${detail ? "  " + detail : ""}`); }
}

async function run(name, errorFactory, { expectCalls } = {}) {
  globalThis.__logs = [];
  globalThis.__calls = 0;
  globalThis.__createEventOriginal = async () => {
    globalThis.__calls++;
    throw errorFactory();
  };
  const started = Date.now();
  let threw = false;
  let errMsg = "";
  try {
    await createEventWithRetry({ id: 1 }, { title: "t" });
  } catch (e) {
    threw = true;
    errMsg = e?.message ?? String(e);
  }
  const elapsed = Date.now() - started;
  return { name, calls: globalThis.__calls, threw, elapsed, expectCalls, errMsg };
}

// --- the policy table ----------------------------------------------------
console.log("=== isTransientCalendarError policy ===");
check("403 rateLimitExceeded -> retryable", isTransientCalendarError(rateLimit403()) === true);
check("403 quotaExceeded -> retryable",
  isTransientCalendarError({ code: 403, errors: [{ reason: "quotaExceeded" }] }) === true);
check("429 -> retryable", isTransientCalendarError({ code: 429 }) === true);
check("500/502/503/504 -> retryable",
  [500, 502, 503, 504].every((c) => isTransientCalendarError({ code: c }) === true));
check("ETIMEDOUT -> retryable", isTransientCalendarError(networkTimeout()) === true);
check("401 -> NOT retryable", isTransientCalendarError(auth401()) === false);
check("400 -> NOT retryable", isTransientCalendarError({ code: 400 }) === false);
check("404 -> NOT retryable", isTransientCalendarError({ code: 404 }) === false);
check("plain 403 (no rate-limit reason) -> NOT retryable", isTransientCalendarError(plain403()) === false);
check("503 -> retryable", isTransientCalendarError(server503()) === true);

console.log("\n=== behaviour (attempts + backoff) ===");
const scenarios = [
  ["rate-limit 403 retries then throws", rateLimit403, 4],
  ["503 retries then throws", server503, 4],
  ["network timeout retries then throws", networkTimeout, 4],
  ["401 fails fast, no retry", auth401, 1],
  ["plain 403 fails fast, no retry", plain403, 1],
];

for (const [name, factoryFn, expected] of scenarios) {
  const r = await run(name, factoryFn);
  check(`${name} -> ${expected} attempt(s)`, r.calls === expected,
    `(calls=${r.calls}, threw=${r.threw}, ${r.elapsed}ms, err=${r.errMsg})`);
}

console.log("\n=== success path is untouched ===");
globalThis.__createEventOriginal = async () => ({ success: true, id: "evt_123" });
globalThis.__calls = 0;
const okResult = await createEventWithRetry({ id: 1 }, { title: "t" });
check("successful create returns the event", okResult?.id === "evt_123");
check("successful create logs nothing", globalThis.__logs.length === 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
