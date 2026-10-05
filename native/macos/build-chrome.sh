#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")" && pwd)"
swift build --package-path "$task_root" --product orf-gateway-app
swift build --package-path "$task_root" --product orf-chrome-host
task_bin="$(swift build --package-path "$task_root" --show-bin-path)"
task_bundle="$task_root/.build/ORF Gateway.app"
mkdir -p "$task_bundle/Contents/MacOS" "$task_root/.build/chrome-extension"
cp "$task_bin/orf-gateway-app" "$task_bundle/Contents/MacOS/ORFGateway"
cp "$task_bin/orf-chrome-host" "$task_bundle/Contents/MacOS/orf-chrome-host"
cp "$task_root/../chrome/Info.plist" "$task_bundle/Contents/Info.plist"
cp "$task_root/../chrome/manifest.json" "$task_root/.build/chrome-extension/manifest.json"
cp "$task_root/Extension/Resources/background.js" "$task_root/Extension/Resources/content.js" "$task_root/Extension/Resources/popup.html" "$task_root/.build/chrome-extension/"
codesign --force --sign - "$task_bundle"
printf 'Built app: %s\nChrome extension: %s\n' "$task_bundle" "$task_root/.build/chrome-extension"
