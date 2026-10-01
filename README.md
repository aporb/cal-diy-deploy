# cal-diy-deploy

Builds the **HARBOR-hardened** `cal.diy` container image for **book.harborgovcon.com** in
GitHub Actions and publishes it to GHCR. The porb-dev server only pulls the image — it never
compiles it (the server is 8 GB and the Next.js build OOMs there).

## Why

- cal.diy publishes **no official Docker image** (Docker Hub `calcom/cal.diy` has zero tags).
- Building on the server OOM-killed Node at ~6 GB during TypeScript checking.
- GitHub's runner is enough **if** type-checking is skipped, which
  `scripts/patch-next-config.py` does for the build only.

## The image is not plain upstream cal.diy

Five build-time patches are applied to the pinned upstream source before the Docker build.
Each re-derives its anchors from that source and **fails the build loudly** if upstream moved
the code, rather than mis-patching it silently.

| Patch | What it fixes |
|---|---|
| `patch-next-config.py` | Skips type-checking so the build fits the runner |
| `patch-calendar-retry.py` | Retries transient calendar failures. Upstream does not retry a `403 rateLimitExceeded` at all, so one throttled Google call lost the event, the Meet link *and* the email |
| `patch-booking-email-send.py` | Sends the confirmation even when every calendar integration failed, and emails the operator when that means there is no join link |
| `patch-reminder-template.py` | Stops reminder emails printing the literal word `undefined` where the Meet link should be |
| `patch-branding.py` | `HARBOR` instead of `Cal.com` in the tab title and the mail sender |

Full background and the decisions behind each layer:
`operations/cal-diy/cal-diy-hardening-spec-2026-10-01.html` in the `2026_books` repo.

### Retry safety

Creating a calendar event is not idempotent, so the retry fires **only** when the failure
proves Google did not process the request: a `403` carrying a rate-limit reason, `429`,
`500/502/503/504`, or a network error. A bare `403`, `401`, `400` or `404` is rethrown
immediately — retrying a dead credential or a malformed request would hide a real fault and
keep the client waiting.

Budget: 4 attempts, ~1s / 2s / 4s backoff, so worst case adds ~7s to a booking.

### Alert transport

The missing-join-link alert posts to the Resend REST API using `RESEND_API_KEY` when set and
otherwise `EMAIL_SERVER_PASSWORD`, which is already in the container. Resend accepts the same
key for the API and the SMTP relay, so **no new environment variable is required** and the
alert cannot shadow cal.com's own mailer.

## Image tags

    ghcr.io/aporb/cal-diy:v6.2.0          # upstream baseline, no patches — keep for rollback
    ghcr.io/aporb/cal-diy:v6.2.0-harbor1  # hardened build

Build from **Actions → Build cal.diy image → Run workflow**.

The workflow separates `source_ref` (the upstream ref to build *from*) from `build_tag` (the
tag to publish). They must differ for a patched build, otherwise you would overwrite the
rollback tag.

`push_latest` defaults to **false** on purpose: production pins an explicit tag, and moving
`:latest` silently would make a rollback ambiguous.

## Deploy (on porb-dev)

`deploy/` mirrors `/opt/cal-diy` on the server:

    deploy/docker-compose.yml     # pulls ghcr.io/aporb/cal-diy
    deploy/.env.example           # copy to /opt/cal-diy/.env and fill secrets
    deploy/Caddyfile.book.snippet # host Caddy block
    deploy/scripts/backup-db.sh   # pg_dump with 14-day retention

## Updating

1. Run the workflow with `source_ref` = the new upstream tag and `build_tag` = e.g.
   `v6.3.0-harbor1`.
2. On the server: bump the image tag in `/opt/cal-diy/docker-compose.yml`, then
   `docker compose pull && docker compose up -d`. Migrations run on start.
3. Confirm the patches still applied by reading the build log's
   "Show exactly what changed" step — it prints the rewritten import, the branding args and
   asserts the unguarded interpolation is gone.

### Rolling back

Pin `ghcr.io/aporb/cal-diy:v6.2.0` in `/opt/cal-diy/docker-compose.yml` and recreate. That
tag is the untouched upstream baseline and is never overwritten by a patched build.
