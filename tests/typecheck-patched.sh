#!/usr/bin/env bash
# Type-check the patched files the way the trpc package build does (ES5 target).
#
# Why this exists: the Docker build runs `yarn workspace @calcom/trpc run build`, which
# type-checks these files with an ES5 target. The web app build skips type-checking
# (patch-next-config.py), so a type error here is ONLY visible by running that package's
# build -- which meant discovering them through a 14-minute image build. This catches them
# in seconds.
#
#   bash tests/typecheck-patched.sh /path/to/patched/src
set -uo pipefail

SRC="${1:-${CALDIY_SRC:-/tmp/e2e/src}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ ! -d "$SRC" ]; then
  echo "source root not found: $SRC"
  exit 2
fi

TSC="$HERE/node_modules/typescript/bin/tsc"
if [ ! -x "$TSC" ]; then
  echo "typescript not installed. run: (cd \"$HERE\" && npm install)"
  exit 2
fi

mkdir -p "$WORK/stubs" "$WORK/src"

# --- faithful stubs for the @calcom/* imports our patched files use -------------------
cat > "$WORK/stubs/calcom.d.ts" <<'DTS'
declare module "@calcom/features/calendars/lib/CalendarManager" {
  export type NewCalendarEventType = { id?: string; hangoutLink?: string; [k: string]: unknown };
  export type EventResult<T> = {
    type: string;
    appName: string;
    success: boolean;
    uid: string;
    createdEvent?: T;
    [k: string]: unknown;
  };
  export type CredentialForCalendarService = { id: number; type: string; [k: string]: unknown };
  export type CalendarEvent = { title: string; uid: string; [k: string]: unknown };
  export const createEvent: (
    credential: CredentialForCalendarService,
    originalEvent: CalendarEvent,
    externalId?: string
  ) => Promise<EventResult<NewCalendarEventType>>;
  export const updateEvent: (...args: unknown[]) => Promise<unknown>;
  export const deleteEvent: (...args: unknown[]) => Promise<unknown>;
}

declare module "@calcom/lib/safeStringify" {
  // Matches upstream: the catch branch returns the raw object, so this is NOT a string.
  export function safeStringify(obj: unknown): unknown;
}

declare const fetch: (
  input: string,
  init?: { method?: string; headers?: Record<string, string>; body?: string }
) => Promise<{ ok: boolean; status: number; text: () => Promise<string> }>;

declare module "@calcom/lib/logger" {
  type Sub = {
    debug: (...a: unknown[]) => void;
    info: (...a: unknown[]) => void;
    warn: (...a: unknown[]) => void;
    error: (...a: unknown[]) => void;
  };
  const logger: { getSubLogger: (o?: unknown) => Sub };
  export default logger;
}
DTS

# --- the files under test, with @calcom/* left for the stubs and relative paths intact ---
cp "$SRC/packages/features/bookings/lib/harbor/createEventWithRetry.ts" "$WORK/src/" 2>/dev/null
cp "$SRC/packages/features/bookings/lib/harbor/alertMissingJoinLink.ts" "$WORK/src/" 2>/dev/null

sed "s|TYPEROOTS|$HERE/node_modules/@types|" > "$WORK/tsconfig.json" <<'JSON'
{
  "compilerOptions": {
    "target": "ES5",
    "lib": ["ES2020", "DOM"],
    "module": "ESNext",
    "moduleResolution": "node",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "types": ["node"],
    "typeRoots": ["TYPEROOTS"]
  },
  "include": ["stubs/**/*.d.ts", "src/**/*.ts"]
}
JSON

echo "Type-checking patched helpers with an ES5 target (matches the trpc build):"
ls "$WORK/src"

set +e
out="$("$TSC" -p "$WORK/tsconfig.json" 2>&1)"
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
  echo "PASS: no type errors"
  exit 0
fi

echo "$out"
echo
echo "FAIL: type errors above would break the image build at 'yarn workspace @calcom/trpc run build'"
exit 1
