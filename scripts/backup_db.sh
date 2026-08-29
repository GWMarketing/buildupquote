#!/usr/bin/env bash
#
# Automated PostgreSQL backup for BuildUpQuote (docker compose).
#
# What it does:
#   1. pg_dump the app database from the `db` compose service
#   2. gzip it to backups/buildupquote_backup_YYYYmmdd_HHMMSS.sql.gz (UTC)
#   3. prune local backups older than 7 days
#   4. if S3_BACKUP_BUCKET is set (and the `aws` CLI is installed), upload
#      the archive to an S3-compatible bucket (AWS S3 / Cloudflare R2 / MinIO)
#
# Usage:
#   ./scripts/backup_db.sh
#
# Environment (optional overrides):
#   BACKUP_DIR        default: <repo>/backups
#   DB_SERVICE        default: db
#   DB_USER           default: app_user
#   DB_NAME           default: buildupquote_db
#   PGPASSWORD        default: app_password (matches docker-compose.yml)
#   KEEP_DAYS         default: 7
#   S3_BACKUP_BUCKET  bucket name; enables upload when set
#   S3_ENDPOINT       e.g. https://<account>.r2.cloudflarestorage.com for R2
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
#
# Scheduling -- run daily at 02:00 UTC. Either:
#
#   Cron (simplest):
#     0 2 * * * cd /var/www/buildupquote && ./scripts/backup_db.sh >> backups/backup.log 2>&1
#
#   systemd (preferred -- runs even if your user isn't logged in):
#     sudo cp deploy/backup.service deploy/backup.timer /etc/systemd/system/
#     sudo systemctl daemon-reload
#     sudo systemctl enable --now backup.timer
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-$REPO_DIR/backups}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-app_user}"
DB_NAME="${DB_NAME:-buildupquote_db}"
KEEP_DAYS="${KEEP_DAYS:-7}"
export PGPASSWORD="${PGPASSWORD:-app_password}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUT_FILE="$BACKUP_DIR/buildupquote_backup_${STAMP}.sql.gz"

echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) dumping $DB_NAME from compose service '$DB_SERVICE'..."
docker compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip > "$OUT_FILE"

SIZE="$(du -h "$OUT_FILE" | cut -f1)"
echo "[backup] wrote $OUT_FILE ($SIZE)"

# Prune local backups older than KEEP_DAYS.
find "$BACKUP_DIR" -name 'buildupquote_backup_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "[backup] pruned local backups older than $KEEP_DAYS days"

# Optional S3-compatible upload.
if [[ -n "${S3_BACKUP_BUCKET:-}" ]]; then
  if ! command -v aws >/dev/null 2>&1; then
    echo "[backup] WARNING: S3_BACKUP_BUCKET is set but the 'aws' CLI is not installed -- skipping upload"
  else
    ARGS=(s3 cp "$OUT_FILE" "s3://${S3_BACKUP_BUCKET}/buildupquote/$(basename "$OUT_FILE")")
    if [[ -n "${S3_ENDPOINT:-}" ]]; then
      ARGS+=(--endpoint-url "$S3_ENDPOINT")
    fi
    echo "[backup] uploading to s3://${S3_BACKUP_BUCKET}/buildupquote/ ..."
    aws "${ARGS[@]}"
    echo "[backup] upload complete"
  fi
fi

echo "[backup] done."
