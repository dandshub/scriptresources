#!/bin/bash
# MacState agent installer for NinjaOne (macOS, run as System).
#
# Add this to the NinjaOne Automation Library as a macOS ShellScript and run
# it from a policy (e.g. on agent install / daily) or on demand. It is
# idempotent: Macs that already run the MacState agent are left alone.
#
# Where the server URL and enrollment key come from, in order:
#   1. script parameters:      <server-url> <enrollment-key>
#      (MacState passes these when you click "Deploy via NinjaOne")
#   2. NinjaOne custom fields:  macstateEnrollmentKey (organization level,
#      filled in by MacState's NinjaOne sync) and optionally macstateServerUrl
#   3. the server URL baked in below when downloaded from MacState
set -euo pipefail

SERVER="${1:-}"
KEY="${2:-}"
DEFAULT_SERVER=__SERVER__
KEY_FIELD=__FIELD__
LABEL="com.macstate.agent"
BIN="/Library/Application Support/MacState/macstate-agent"

cli="${NINJA_DATA_PATH:-/Applications/NinjaRMMAgent/programdata}/ninjarmm-cli"
field() {
  [[ -x "$cli" ]] || return 0
  local v
  v="$("$cli" get "$1" 2>/dev/null || true)"
  [[ "$v" == *"Unable to find"* ]] && v=""
  printf '%s' "$v" | tr -d '\r\n'
}

[[ -z "$KEY" ]] && KEY="$(field "$KEY_FIELD")"
[[ -z "$SERVER" ]] && SERVER="$(field macstateServerUrl)"
[[ -z "$SERVER" ]] && SERVER="$DEFAULT_SERVER"
SERVER="${SERVER%/}"

if [[ -z "$KEY" ]]; then
  echo "No MacState enrollment key: pass it as a parameter or run a MacState NinjaOne sync" >&2
  echo "so the organization custom field '$KEY_FIELD' is filled in." >&2
  exit 1
fi

if [[ -x "$BIN" ]] && launchctl print "system/$LABEL" >/dev/null 2>&1; then
  echo "MacState agent already installed ($("$BIN" version)); nothing to do."
  exit 0
fi

echo "Installing MacState agent from $SERVER"
curl -fsSL --retry 3 "$SERVER/agent/install.sh?key=$KEY" -o /tmp/macstate-install.sh
/bin/bash /tmp/macstate-install.sh
rm -f /tmp/macstate-install.sh
