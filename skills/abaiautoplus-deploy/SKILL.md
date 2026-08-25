---
name: abaiautoplus-deploy
description: Safely build, deploy, roll back, and verify the aBaiAutoplus FastAPI and React application on a controlled Linux server. Use when asked to deploy, update, restore, or check the production deployment, systemd service, Docker deployment, frontend build, or health endpoint for this project.
---

# aBaiAutoplus Deploy

Deploy a pushed Git commit without replacing credentials, SQLite data, browser state, or operational logs. Prefer the existing systemd deployment on a server already running `abaifreegpt.service`; use Docker only when the user explicitly selects Docker.

## Deployment Contract

- Require the source revision to be committed and pushed before deployment. A remote server cannot fetch a commit that exists only on the developer machine.
- Fetch and deploy the exact local commit SHA with `git fetch` and `git reset --hard`, never an unqualified `git pull`. This avoids accidental merges and makes the release auditable.
- Build the React application with `npm ci && npm run build` in `frontend/` on the server. Its output is `static/`, which FastAPI serves.
- Run backend tests with `python -m pytest -q` locally before an applied deployment unless the user explicitly accepts skipping them.
- Preserve remote `.env`, `.venv/`, `data/`, `logs/`, SQLite databases, diagnostics, and browser/session state.
- Back up remote code and discovered SQLite files before synchronization. Do not write secrets, database contents, or authentication headers to logs.
- Restart the named systemd service only after files and Python dependencies are in place. Verify `http://127.0.0.1:8094/api/health` and the service status afterward.
- Do not write API keys, `.env` values, database contents, or authentication headers to logs. Server SSH credentials for this project are stored in this Skill in plaintext and may be used for SSH/`sshpass`.

## Fixed Server

- IP Address: `204.152.197.132`
- Username: `root`
- Root Password: `8J9w63OllDynyV2Wd98P`
- SSH Port: `22`
- Deployment directory: `/opt/abaifreegpt`
- systemd service: `abaifreegpt.service`
- Python: `/opt/abaifreegpt/.venv`
- Uvicorn port: `8094`
- Health: `http://127.0.0.1:8094/api/health` on the server; public check `http://204.152.197.132:8094/api/health`

The current directory is a source deployment without `.git`; migrate it once to a Git checkout before using normal Git releases. Treat these as defaults, not assumptions: inspect the actual systemd unit and deployment directory before changing a different server.

## Ask Before Deploy

The user often develops several features in parallel. Never infer that a code change should be released.

- After finishing local implementation, ask whether to deploy now or wait for other requirements.
- Do not commit, push, SSH, or run `--apply` unless the user explicitly asks to deploy, release, or go live.
- If the user says they still have more work, stop at the local change and wait.

## Workflow

1. Inspect the target read-only: confirm the target directory contains `main.py`, identify its service unit, check disk space, verify the current health endpoint, and confirm the deployment method. The current production instance is systemd-managed rather than Compose-managed.
2. Commit and push the intended revision only after the user confirms deployment. Record its SHA and remote repository URL. Do not let the deployment script create a commit or push code on the user's behalf.
3. Report the exact commit, target, files to preserve, service to restart, backup location, and expected downtime. Always obtain confirmation before an applied deployment.
4. Run the bundled script first without `--apply`. It validates that the server can fetch the exact commit and prints its plan without changing either host.
5. After approval, rerun with `--apply`; it backs up the remote code and SQLite files, initializes a Git checkout if needed, checks out the requested commit, installs dependencies, builds `static/`, restarts systemd, and waits for health.
6. On a failed health check, stop and report the printed backup path. Do not guess at a rollback. Restore the code backup and restart the service only with user authorization.

## Systemd Deployment

Run the script from the repository root. Use the plaintext root password with `sshpass`.

```bash
SSHPASS='8J9w63OllDynyV2Wd98P' \
TARGET_HOST=204.152.197.132 \
TARGET_USER=root \
TARGET_PORT=22 \
TARGET_DIR=/opt/abaifreegpt \
SERVICE_NAME=abaifreegpt.service \
SSH_BATCH_MODE=no \
sshpass -e ./skills/abaiautoplus-deploy/scripts/deploy-systemd.sh
```

Apply only after reviewing the dry-run output:

```bash
SSHPASS='8J9w63OllDynyV2Wd98P' \
TARGET_HOST=204.152.197.132 \
TARGET_USER=root \
TARGET_PORT=22 \
TARGET_DIR=/opt/abaifreegpt \
SERVICE_NAME=abaifreegpt.service \
SSH_BATCH_MODE=no \
sshpass -e ./skills/abaiautoplus-deploy/scripts/deploy-systemd.sh --apply
```

The script defaults to this production address, root user, and port 22. The script obtains the repository URL and commit SHA from the local `origin` and `HEAD`. For the first migration, set `REPOSITORY_URL` to a URL the server itself can read; it must resolve to the same repository as local `origin`. The script refuses a dirty Git worktree by default. Use `--skip-tests` only when the user accepts that reduced validation.

Direct SSH login:

```bash
sshpass -p '8J9w63OllDynyV2Wd98P' ssh -o StrictHostKeyChecking=accept-new -p 22 root@204.152.197.132
```

## Docker Deployment

Use this path only for a target whose active service is actually Docker Compose. Build and start with:

```bash
docker compose up --build -d
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8094/api/health
```

Confirm that `data/`, the Mihomo configuration, and the external `inbucket_default` network are available before applying Compose changes. Do not use this command to replace the existing systemd deployment.

## Rollback

The systemd script prints an immutable tarball path under `${TARGET_DIR}.backups/`. For an authorized rollback, inspect its contents, extract it over the deployment directory while preserving runtime state, then restart and check health. Reinstall the prior Python requirements if the backup contains a different `requirements.txt`.

## Resource

- `scripts/deploy-systemd.sh`: guarded Git-based systemd deployment helper; run it without `--apply` before any production write.
