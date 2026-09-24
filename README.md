# cal-diy-deploy

Builds the `cal.diy` container image for **book.harborgovcon.com** in GitHub Actions and
publishes it to GHCR. The porb-dev server only pulls the image — it never compiles it
(the server is 8 GB and the Next.js build OOMs there).

## Why

- cal.diy publishes **no official Docker image** (Docker Hub `calcom/cal.diy` has zero tags).
- Building on the server OOM-killed Node at ~6 GB during TypeScript checking.
- GitHub's private-repo runner (2 vCPU / 8 GB) is enough **if** type-checking is skipped,
  which `scripts/patch-next-config.py` does for the build only.

## Image

    ghcr.io/aporb/cal-diy:v6.2.0
    ghcr.io/aporb/cal-diy:latest

Public package. Build it from **Actions → Build cal.diy image → Run workflow**.

## Deploy (on porb-dev)

`deploy/` mirrors `/opt/cal-diy` on the server:

    deploy/docker-compose.yml     # pulls ghcr.io/aporb/cal-diy
    deploy/.env.example           # copy to /opt/cal-diy/.env and fill secrets
    deploy/Caddyfile.book.snippet # host Caddy block
    deploy/scripts/backup-db.sh   # pg_dump with 14-day retention

## Updating

1. Run the workflow with a new `tag` (e.g. `v6.3.0`).
2. On the server: bump the image tag in `/opt/cal-diy/docker-compose.yml`, then
   `docker compose pull && docker compose up -d`. Migrations run on start.
