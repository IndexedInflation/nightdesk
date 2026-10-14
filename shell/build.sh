#!/usr/bin/env bash
# Optional native Mac window. The source folder can be moved as a whole.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="$ROOT/.build/mac"
APP="$ROOT/Nightdesk.app"
[[ "$(uname -s)" == "Darwin" ]] || { echo "The native window is for macOS. Use Launch.command --browser instead." >&2; exit 1; }
command -v swiftc >/dev/null || { echo "Install Apple's optional command line tools: xcode-select --install" >&2; exit 1; }
ARCH="$(uname -m)"
case "$ARCH" in arm64|x86_64) ;; *) echo "Unsupported Mac architecture: $ARCH" >&2; exit 1 ;; esac
for tool in sips iconutil codesign; do command -v "$tool" >/dev/null || { echo "Missing Apple build tool: $tool" >&2; exit 1; }; done
mkdir -p "$BUILD/module-cache" "$BUILD/Nightdesk.iconset"
export CLANG_MODULE_CACHE_PATH="$BUILD/module-cache"
echo "Compiling the Mac window for $ARCH..."
swiftc -O -swift-version 5 -target "$ARCH-apple-macos13.0" -module-cache-path "$BUILD/module-cache" -o "$BUILD/Nightdesk" "$HERE/Nightdesk.swift" -framework Cocoa -framework WebKit
swiftc -O -swift-version 5 -target "$ARCH-apple-macos13.0" -module-cache-path "$BUILD/module-cache" -o "$BUILD/make_icon" "$HERE/make_icon.swift" -framework Cocoa
"$BUILD/make_icon" "$BUILD/icon-1024.png"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$BUILD/icon-1024.png" --out "$BUILD/Nightdesk.iconset/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "$BUILD/icon-1024.png" --out "$BUILD/Nightdesk.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$BUILD/Nightdesk.iconset" -o "$BUILD/Nightdesk.icns"
# Build completely before replacing a previous local wrapper.
STAGED_APP="$BUILD/Nightdesk.app"
mkdir -p "$STAGED_APP/Contents/MacOS" "$STAGED_APP/Contents/Resources"
cp "$BUILD/Nightdesk" "$STAGED_APP/Contents/MacOS/Nightdesk"
cp "$BUILD/Nightdesk.icns" "$STAGED_APP/Contents/Resources/Nightdesk.icns"
cat > "$STAGED_APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Nightdesk</string>
  <key>CFBundleDisplayName</key><string>Nightdesk</string>
  <key>CFBundleIdentifier</key><string>local.nightdesk.app</string>
  <key>CFBundleExecutable</key><string>Nightdesk</string>
  <key>CFBundleIconFile</key><string>Nightdesk</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Practice trading with pretend money.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
codesign --force --sign - "$STAGED_APP"
if [[ -e "$APP" ]]; then rm -rf -- "$APP"; fi
cp -R "$STAGED_APP" "$APP"
rm -f "$ROOT/.native-disabled"
echo "Built the optional Nightdesk.app. Keep it beside engine/ and .venv/, or open it using Launch.command."
