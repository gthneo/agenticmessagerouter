#!/bin/bash
# Deploy/update AMR on a target host: sync code, then install/refresh the
# systemd --user services. Assumes you already have SSH access to the target
# (key or agent) — credentials are never stored in the repo.
#
#   deploy/deploy.sh dbos-user@192.168.31.178
#
# Requires: python3.10+ on the target (zero runtime deps). The target's
# fullwechat backend must be reachable at http://localhost:6174 with its token
# at ~/.config/agent-wechat/token.
set -euo pipefail

TARGET="${1:?usage: deploy.sh user@host}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ syncing code to $TARGET:~/amr"
rsync -az --delete --exclude='__pycache__' \
    "$ROOT/src/jl" "$ROOT/pyproject.toml" "$ROOT/deploy" \
    "$TARGET:~/amr/"

echo "→ installing/refreshing systemd --user services on $TARGET"
ssh "$TARGET" 'bash ~/amr/deploy/install.sh'
