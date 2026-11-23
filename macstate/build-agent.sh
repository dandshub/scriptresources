#!/usr/bin/env bash
# Cross-compile the macOS agent (arm64 + amd64) into ./build, where the server
# serves it to installers and to agents for self-update. Needs Go >= 1.22.
#
#   ./build-agent.sh            # version = git describe / timestamp
#   VERSION=1.2.0 ./build-agent.sh
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${VERSION:-$(git describe --tags --always --dirty 2>/dev/null || date +%Y%m%d%H%M%S)-$(date +%Y%m%d%H%M%S)}"
OUT="${MACSTATE_AGENT_DIR:-build}"
mkdir -p "$OUT"
for arch in arm64 amd64; do
  echo "building darwin/$arch ($VERSION)"
  (cd agent && CGO_ENABLED=0 GOOS=darwin GOARCH=$arch \
    go build -trimpath -ldflags "-s -w -X main.Version=$VERSION" \
    -o "../$OUT/macstate-agent-darwin-$arch.tmp" .)
  mv -f "$OUT/macstate-agent-darwin-$arch.tmp" "$OUT/macstate-agent-darwin-$arch"
done
echo "$VERSION" > "$OUT/VERSION"
echo "done: $OUT/"
