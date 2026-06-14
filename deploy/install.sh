#!/bin/bash
# Install/refresh AMR systemd --user services on THIS host. Idempotent.
# Run on the target box (e.g. .178) after the code is synced to ~/amr.
# Generates the web token on first run; never reads secrets from the repo.
set -euo pipefail

APP="$HOME/amr"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$HOME/.config/jl/amr.env"
TOKEN_FILE="$HOME/.config/jl/web_token"
PY="$(command -v python3)"

mkdir -p "$UNIT_DIR" "$HOME/.config/jl"

# web access token — generated once, kept out of the repo
if [ ! -f "$TOKEN_FILE" ]; then
    python3 -c 'import secrets; print(secrets.token_hex(16))' > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
fi
printf 'JL_WEB_TOKEN=%s\n' "$(cat "$TOKEN_FILE")" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# render units (substitute the real python path) into the user unit dir
for unit in amr-web amr-poll; do
    sed "s#__PYTHON__#$PY#" "$APP/deploy/systemd/$unit.service" > "$UNIT_DIR/$unit.service"
done

# survive logout AND reboot
loginctl enable-linger "$USER" 2>/dev/null || true

# migrate off any old nohup-launched instances so they don't hold :8088
pkill -f 'jl.cli web'  2>/dev/null || true
pkill -f 'jl.cli poll' 2>/dev/null || true
sleep 1

systemctl --user daemon-reload
systemctl --user enable --now amr-web.service amr-poll.service
systemctl --user restart amr-web.service amr-poll.service

systemctl --user --no-pager --lines=0 status amr-web.service amr-poll.service | grep -E 'amr-|Active:' || true
echo "AMR web token: $(cat "$TOKEN_FILE")"
echo "Inbox: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8088/?token=$(cat "$TOKEN_FILE")"
