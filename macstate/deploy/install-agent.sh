#!/bin/bash
# MacState agent installer. Served by the MacState server with the server URL
# and tenant enrollment key filled in:
#
#   curl -fsSL 'https://macstate.example.com/agent/install.sh?key=KEY' | sudo bash
#
# Also works as an MDM (Jamf/Intune/Kandji/Mosyle) shell script, which runs as root.
set -euo pipefail

SERVER=__SERVER__
KEY=__KEY__
LABEL="com.macstate.agent"
DIR="/Library/Application Support/MacState"
BIN="$DIR/macstate-agent"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
LOGDIR="/Library/Logs/MacState"

if [[ $EUID -ne 0 ]]; then
  echo "error: run as root (pipe to 'sudo bash')" >&2
  exit 1
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: this installer is for macOS" >&2
  exit 1
fi

case "$(uname -m)" in
  arm64) ARCH=arm64 ;;
  x86_64) ARCH=amd64 ;;
  *) echo "error: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$DIR" "$LOGDIR"
chmod 755 "$DIR"

echo "Downloading MacState agent ($ARCH) from $SERVER ..."
tmp="$(mktemp "$DIR/agent.XXXXXX")"
curl -fsSL --retry 3 "$SERVER/agent/download/$ARCH" -o "$tmp"
chmod 755 "$tmp"
chown root:wheel "$tmp"
xattr -c "$tmp" 2>/dev/null || true

# Stop an existing agent before replacing the binary.
launchctl bootout "system/$LABEL" 2>/dev/null || true
mv -f "$tmp" "$BIN"

echo "Enrolling with $SERVER ..."
"$BIN" enroll --server "$SERVER" --key "$KEY"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOGDIR/agent.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/agent.log</string>
</dict>
</plist>
PLIST
chown root:wheel "$PLIST"
chmod 644 "$PLIST"

launchctl bootstrap system "$PLIST"
launchctl enable "system/$LABEL" 2>/dev/null || true
echo "MacState agent installed and running. Log: $LOGDIR/agent.log"
