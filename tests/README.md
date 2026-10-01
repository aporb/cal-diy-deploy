# Patch tests

These run the **real patched files**, not copies. Each suite points at a patched source
tree, strips TypeScript with the actual compiler, stubs the `@calcom/*` imports, and then
executes the code so the assertions are about behaviour rather than parse success.

They exist because every bug found while writing the patches was a *behavioural* one that a
parse check would have missed:

- the reminder helper was initially installed **inside** the template function instead of at
  module scope, so it was unreachable — the file still parsed fine
- the retry wrapper's policy had to be right about which errors are safe to retry; a wrong
  answer either creates duplicate calendar events or hides a dead credential

## Requirements

    npm install

`typescript` transpiles the patched source; `dayjs` is a real dependency of the reminder
template, so that suite renders genuine output.

## Running

    # 1. produce a patched tree
    cp -r patch-assets <src>/patch-assets
    python3 scripts/patch-calendar-retry.py <src>
    python3 scripts/patch-booking-email-send.py <src>
    python3 scripts/patch-reminder-template.py <src>

    # 2. run everything
    CALDIY_SRC=<src> bash tests/run-tests.sh

## What each suite covers

| Suite | Asserts |
|---|---|
| `typecheck-patched.sh` | The patched helpers compile under an **ES5** target with `strict`. This mirrors `yarn workspace @calcom/trpc run build`, which is what the image build runs — and the only thing that type-checks patched code, since the web build skips type-checking |
| `verify-ast.mjs` | Every patched file parses. The email send is **not** nested in an `else`. The alert is wired in. `EventManager` routes `createEvent` through the retry wrapper. The unguarded `${meetingUrl}` interpolation is gone |
| `test-retry.mjs` | The policy table: 403+rate-limit-reason, 429, 500/502/503/504 and network errors retry; bare 403, 401, 400, 404 do not. Measured behaviour: transient → 4 attempts over ~7s, permanent → 1 attempt and 0ms. Success path is untouched and logs nothing |
| `test-reminder.mjs` | With `meetingUrl` undefined the rendered email contains **no** "undefined" and no "Google Meet undefined", still names the location, and includes the link when one exists. Editing-mode preview does not print a placeholder artifact |
| `test-alert.mjs` | Posts to the Resend API with the right recipient and a useful body. Falls back to `EMAIL_SERVER_PASSWORD` when `RESEND_API_KEY` is unset. Never throws — with no credential, on HTTP 500, or when `fetch` itself throws |

## Notes

`verify-ast.mjs` and the suites are deliberately independent of the patch scripts: they test
the *output* of patching, so a script that silently mis-anchors is caught here rather than in
production.
