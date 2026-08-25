#!/usr/bin/env bash
# Deploy a pushed aBaiAutoplus Git revision to an existing systemd deployment.
# Dry-run is the default. --apply is required before any remote modification.
set -euo pipefail

apply=false
skip_tests=false

usage() {
  cat <<'USAGE'
Usage: deploy-systemd.sh [--apply] [--skip-tests]

Optional environment:
  TARGET_HOST       SSH host name or IP address (default: 204.152.197.132)
  TARGET_USER       SSH user (default: root)
  TARGET_PORT       SSH port (default: 22)
  SSH_BATCH_MODE    Set to no to allow an interactive SSH password prompt
  TARGET_DIR        Deployment directory (default: /opt/abaifreegpt)
  SERVICE_NAME      systemd unit (default: abaifreegpt.service)
  TARGET_BRANCH     Pushed branch containing HEAD (default: current branch)
  REPOSITORY_URL    Clone URL reachable from the server (default: local origin)
  PYTHON_BIN        Local Python executable (default: python3)

The local worktree must be clean and HEAD must equal the pushed target branch.
Without --apply, run only local and remote preflight checks.
USAGE
}

while (($#)); do
  case "$1" in
    --apply) apply=true ;;
    --skip-tests) skip_tests=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

target_host="${TARGET_HOST:-204.152.197.132}"
target_user="${TARGET_USER:-root}"
target_port="${TARGET_PORT:-22}"
ssh_batch_mode="${SSH_BATCH_MODE:-yes}"
target_dir="${TARGET_DIR:-/opt/abaifreegpt}"
service_name="${SERVICE_NAME:-abaifreegpt.service}"
python_bin="${PYTHON_BIN:-python3}"

case "$target_dir" in
  /|.|..|""|*'..'*)
    printf 'Refusing unsafe TARGET_DIR: %s\n' "$target_dir" >&2
    exit 2
    ;;
  /*) ;;
  *)
    printf 'TARGET_DIR must be an absolute path: %s\n' "$target_dir" >&2
    exit 2
    ;;
esac

for command in git ssh npm "$python_bin"; do
  command -v "$command" >/dev/null || {
    printf 'Missing required local command: %s\n' "$command" >&2
    exit 1
  }
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
test -f main.py
test -f requirements.txt
test -f frontend/package.json

if [[ -n "$(git status --porcelain)" ]]; then
  printf 'Refusing dirty worktree. Commit or stash changes before deployment.\n' >&2
  exit 1
fi

revision="$(git rev-parse HEAD)"
target_branch="${TARGET_BRANCH:-$(git branch --show-current)}"
if [[ -z "$target_branch" ]]; then
  printf 'Set TARGET_BRANCH when deploying from a detached HEAD.\n' >&2
  exit 2
fi
repository_url="${REPOSITORY_URL:-$(git remote get-url origin)}"
if [[ -z "$repository_url" ]]; then
  printf 'Set REPOSITORY_URL or configure the local origin remote.\n' >&2
  exit 2
fi

remote_branch_revision="$(git ls-remote "$repository_url" "refs/heads/$target_branch" | awk 'NR == 1 { print $1 }')"
if [[ "$remote_branch_revision" != "$revision" ]]; then
  printf 'HEAD (%s) is not the pushed tip of %s (%s). Commit and push first.\n' \
    "$revision" "$target_branch" "${remote_branch_revision:-missing}" >&2
  exit 1
fi

ssh_target="${target_user}@${target_host}"
ssh_opts=(-p "$target_port" -o "BatchMode=$ssh_batch_mode" -o ConnectTimeout=15)

remote_preflight() {
  ssh "${ssh_opts[@]}" "$ssh_target" \
    "TARGET_DIR='$target_dir' SERVICE_NAME='$service_name' REPOSITORY_URL='$repository_url' TARGET_BRANCH='$target_branch' REVISION='$revision' bash -s" <<'REMOTE'
set -euo pipefail
case "$TARGET_DIR" in
  /|.|..|""|*'..'*) echo "Unsafe TARGET_DIR: $TARGET_DIR" >&2; exit 2 ;;
esac
test -d "$TARGET_DIR"
test -f "$TARGET_DIR/main.py"
test -x "$TARGET_DIR/.venv/bin/python"
command -v git >/dev/null
command -v npm >/dev/null
systemctl status "$SERVICE_NAME" --no-pager >/dev/null
df -h "$TARGET_DIR"
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8094/api/health >/dev/null
remote_ref="$(git ls-remote "$REPOSITORY_URL" "refs/heads/$TARGET_BRANCH" | awk 'NR == 1 { print $1 }')"
test "$remote_ref" = "$REVISION"
if [ -d "$TARGET_DIR/.git" ]; then
  deployed_origin="$(git -C "$TARGET_DIR" remote get-url origin)"
  echo "Existing Git checkout: $deployed_origin"
else
  echo "Source deployment detected; --apply will migrate it to a Git checkout."
fi
echo "Remote preflight passed: $TARGET_DIR ($SERVICE_NAME)"
REMOTE
}

printf 'Commit: %s (%s)\n' "$revision" "$target_branch"
printf 'Repository: %s\n' "$repository_url"
printf 'Target: %s:%s%s\n' "$target_host" "$target_port" "$target_dir"
printf 'Service: %s\n' "$service_name"
printf 'Mode: %s\n' "$($apply && printf apply || printf dry-run)"
remote_preflight

if ! $apply; then
  cat <<'PLAN'

Dry-run passed. --apply will:
  1. run pytest locally;
  2. back up the remote code and SQLite files;
  3. initialize a Git checkout if the target is a source deployment;
  4. fetch and reset to the exact commit shown above;
  5. install requirements, build frontend/static, restart systemd, and verify health.
PLAN
  exit 0
fi

if ! $skip_tests; then
  "$python_bin" -m pytest -q
fi

if ! ssh "${ssh_opts[@]}" "$ssh_target" \
  "TARGET_DIR='$target_dir' SERVICE_NAME='$service_name' REPOSITORY_URL='$repository_url' REVISION='$revision' bash -s" <<'REMOTE'
set -euo pipefail
backup_dir="${TARGET_DIR}.backups"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="$(basename "$TARGET_DIR")"
parent="$(dirname "$TARGET_DIR")"
code_backup="$backup_dir/${base}-${timestamp}.tar.gz"
previous_dir="$backup_dir/${base}-${timestamp}.previous"
mkdir -p "$backup_dir"

report_failure() {
  exit_code=$?
  trap - ERR
  printf 'Deployment failed. Code backup: %s\n' "$code_backup" >&2
  systemctl status "$SERVICE_NAME" --no-pager >&2 || true
  exit "$exit_code"
}
trap report_failure ERR

tar -C "$parent" -czf "$code_backup" \
  --exclude="$base/.env" \
  --exclude="$base/.env.*" \
  --exclude="$base/.venv" \
  --exclude="$base/data" \
  --exclude="$base/logs" \
  --exclude="$base/*.db" \
  --exclude="$base/browser-diagnostics" \
  --exclude="$base/sentinel-sdk" \
  "$base"
for db in "$TARGET_DIR"/*.db "$TARGET_DIR"/data/*.db; do
  [ -f "$db" ] || continue
  destination="$backup_dir/${base}-${timestamp}-$(basename "$db")"
  if command -v sqlite3 >/dev/null; then
    sqlite3 "$db" ".backup '$destination'"
  else
    cp -p "$db" "$destination"
  fi
done

if [ -d "$TARGET_DIR/.git" ]; then
  git -C "$TARGET_DIR" fetch --prune origin
  git -C "$TARGET_DIR" cat-file -e "$REVISION^{commit}"
  systemctl stop "$SERVICE_NAME"
  git -C "$TARGET_DIR" reset --hard "$REVISION"
  git -C "$TARGET_DIR" clean -ffd
else
  staging_dir="${TARGET_DIR}.staging-${timestamp}"
  git clone --no-checkout "$REPOSITORY_URL" "$staging_dir"
  git -C "$staging_dir" checkout --detach "$REVISION"
  systemctl stop "$SERVICE_NAME"
  mv "$TARGET_DIR" "$previous_dir"
  mv "$staging_dir" "$TARGET_DIR"
  for state in .env .venv data logs account_manager.db browser-diagnostics sentinel-sdk; do
    if [ -e "$previous_dir/$state" ]; then
      mv "$previous_dir/$state" "$TARGET_DIR/$state"
    fi
  done
fi

cd "$TARGET_DIR"
.venv/bin/python -m pip install -r requirements.txt
(cd frontend && npm ci && npm run build)
test -f static/index.html
systemctl start "$SERVICE_NAME"
for _ in $(seq 1 20); do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8094/api/health >/dev/null; then
    systemctl is-active --quiet "$SERVICE_NAME"
    echo "Deployment healthy. Code backup: $code_backup"
    exit 0
  fi
  sleep 1
done
echo "Deployment health check timed out" >&2
false
REMOTE
then
  printf 'Deployment failed; inspect remote backups under %s.backups/.\n' "$target_dir" >&2
  exit 1
fi
