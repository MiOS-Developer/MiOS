#!/usr/bin/env bash
# AI-hint: Creates or refreshes a pull mirror of the MiOS GitHub repository in the local Forgejo instance.
set -uo pipefail

ENV_FILE=/etc/mios/install.env
# shellcheck source=/dev/null
[ -r "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

FORGE_PORT="${MIOS_PORTS_FORGE_HTTP:-${MIOS_PORT_FORGE_HTTP:-8400}}"
FORGE="${FORGE_URL:-http://localhost:${FORGE_PORT}}"
SRC="${GITHUB_URL:-https://github.com/mios-dev/MiOS.git}"
REPONAME="${MIRROR_NAME:-MiOS}"

FORGE_EXEC=(podman exec --user 816:816 mios-forge forgejo --config /data/gitea/conf/app.ini)

OWNER="${FORGE_OWNER:-${MIOS_FORGE_ADMIN_USER:-${MIOS_LINUX_USER:-${MIOS_IDENTITY_USERNAME:-}}}}"
if [ -z "$OWNER" ]; then
  OWNER="$("${FORGE_EXEC[@]}" admin user list 2>/dev/null | awk 'NR>1 && $5=="true"{print $2; exit}')"
fi
[ -n "$OWNER" ] || { echo "ERROR: could not resolve forge owner from SSOT" >&2; exit 1; }
echo "Forge=$FORGE  owner=$OWNER  mirror=$OWNER/$REPONAME  src=$SRC"

TOKEN="$("${FORGE_EXEC[@]}" admin user generate-access-token \
          --username "$OWNER" --token-name "mios-a2o-mirror-$(date +%s)" \
          --scopes write:repository --raw 2>/tmp/mint.err | tr -d '[:space:]')"
if [ -z "$TOKEN" ]; then echo "ERROR: token mint failed:" >&2; cat /tmp/mint.err >&2; exit 1; fi

payload="{\"clone_addr\":\"$SRC\",\"repo_name\":\"$REPONAME\",\"repo_owner\":\"$OWNER\",\"mirror\":true,\"private\":false,\"service\":\"git\""
[ -n "${GH_TOKEN:-}" ] && payload="$payload,\"auth_token\":\"$GH_TOKEN\""
payload="$payload}"

code=$(curl -sS -o /tmp/forge-mirror.json -w '%{http_code}' \
  -H "Authorization: token $TOKEN" -H 'Content-Type: application/json' \
  -X POST "$FORGE/api/v1/repos/migrate" -d "$payload" || echo 000)
case "$code" in
  201) echo "OK: mirror created -> $FORGE/$OWNER/$REPONAME";;
  409) echo "Exists: triggering mirror-sync for $OWNER/$REPONAME"
       curl -sS -H "Authorization: token $TOKEN" -X POST "$FORGE/api/v1/repos/$OWNER/$REPONAME/mirror-sync" >/dev/null && echo "OK: sync triggered";;
  401|403) echo "AUTH FAILED — token scope/owner issue"; exit 1;;
  422) echo "HTTP 422:"; cat /tmp/forge-mirror.json; exit 1;;
  *) echo "FAILED HTTP $code:"; cat /tmp/forge-mirror.json 2>/dev/null; exit 1;;
esac
