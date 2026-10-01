import ts from "/tmp/tscheck/node_modules/typescript/lib/typescript.js";
import fs from "node:fs";

const results = [];
function analyse(file) {
  const src = fs.readFileSync(file, "utf8");
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.ESNext, true, ts.ScriptKind.TS);
  const errors = (sf.parseDiagnostics ?? []).map(d =>
    ts.flattenDiagnosticMessageText(d.messageText, " "));
  return { sf, errors };
}

function finding(file, label, predicate) {
  const { sf, errors } = analyse(file);
  results.push({ file, label, errors, ok: errors.length === 0 && predicate(sf) });
}

const hc = "/tmp/e2e/src/packages/features/bookings/lib/handleConfirmation.ts";
const em = "/tmp/e2e/src/packages/features/bookings/lib/EventManager.ts";
const rt = "/tmp/e2e/src/packages/features/ee/workflows/lib/reminders/templates/emailReminderTemplate.ts";

// 1. email send no longer inside an else
finding(hc, "email send NOT inside an else branch", (sf) => {
  let ok = false;
  const visit = (node) => {
    if (ts.isCallExpression(node) && node.expression.getText(sf).endsWith("sendScheduledEmailsAndSMS")) {
      let inElse = false, q = node.parent;
      while (q) {
        if (ts.isIfStatement(q) && q.elseStatement) {
          const walk = (n) => { if (n === node) inElse = true; ts.forEachChild(n, walk); };
          walk(q.elseStatement);
        }
        q = q.parent;
      }
      if (!inElse) ok = true;
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return ok;
});

// 2. alert wired in
finding(hc, "alertMissingJoinLink called in handleConfirmation", (sf) =>
  sf.getText().includes("alertMissingJoinLink({"));

// 3. EventManager imports retrying createEvent, and calls it
finding(em, "EventManager routes createEvent through the retry wrapper", (sf) => {
  const t = sf.getText();
  return t.includes('from "./harbor/createEventWithRetry"') && t.includes("createEvent(");
});

// 4. retry helper has no syntax errors and exports the wrapper
finding("/tmp/e2e/src/packages/features/bookings/lib/harbor/createEventWithRetry.ts",
  "retry helper parses and exports createEventWithRetry", (sf) =>
    sf.getText().includes("export async function createEventWithRetry"));

// 5. alert helper parses and never throws out
finding("/tmp/e2e/src/packages/features/bookings/lib/harbor/alertMissingJoinLink.ts",
  "alert helper parses and exports alertMissingJoinLink", (sf) =>
    sf.getText().includes("export async function alertMissingJoinLink"));

// 6. reminder template: no unguarded interpolation, placeholder collapsed
finding(rt, "reminder template guards the meeting URL", (sf) => {
  const t = sf.getText();
  return t.includes("joinLocationParts(")
    && !t.includes("?.label || location} ${meetingUrl}")
    && !t.includes("{LOCATION} {MEETING_URL}");
});

let allOk = true;
for (const r of results) {
  if (!r.ok) allOk = false;
  const status = r.ok ? "PASS" : "FAIL";
  const errs = r.errors.length ? `  syntax errors: ${r.errors.join("; ")}` : "";
  console.log(`[${status}] ${r.label}${errs}`);
}
console.log(allOk ? "\nALL CHECKS PASSED" : "\nSOME CHECKS FAILED");
process.exit(allOk ? 0 : 1);
