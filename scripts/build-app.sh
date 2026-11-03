#!/bin/bash
# Build RooksCompanion.app from the Swift package
# Usage: scripts/build-app.sh [--release]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MACOS_DIR="$REPO_ROOT/companion/macos"
BUILD_DIR="$MACOS_DIR/.build"
APP_NAME="RooksCompanion"
BUNDLE_ID="com.inclined88888.rooks.companion"

CONFIGURATION="debug"
if [[ "${1:-}" == "--release" ]]; then
    CONFIGURATION="release"
fi

echo "Building $APP_NAME ($CONFIGURATION)..."

# Build the Swift package
cd "$MACOS_DIR"
swift build -c "$CONFIGURATION"

# Find the built binary
BINARY_PATH="$BUILD_DIR/$CONFIGURATION/$APP_NAME"
if [[ ! -f "$BINARY_PATH" ]]; then
    echo "Error: Binary not found at $BINARY_PATH"
    exit 1
fi

# Create app bundle
APP_BUNDLE="$BUILD_DIR/$CONFIGURATION/$APP_NAME.app"
CONTENTS_DIR="$APP_BUNDLE/Contents"
MACOS_DIR_IN_BUNDLE="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

rm -rf "$APP_BUNDLE"
mkdir -p "$MACOS_DIR_IN_BUNDLE"
mkdir -p "$RESOURCES_DIR"

# Copy binary
cp "$BINARY_PATH" "$MACOS_DIR_IN_BUNDLE/$APP_NAME"
chmod +x "$MACOS_DIR_IN_BUNDLE/$APP_NAME"

# Create Info.plist
cat > "$CONTENTS_DIR/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>
    <string>Rooks Companion</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>ROOK</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
EOF

# Create a simple app icon (using system symbol)
# We'll create a basic icon using iconutil if we have an iconset
ICONSET_DIR="$RESOURCES_DIR/AppIcon.iconset"
mkdir -p "$ICONSET_DIR"

# Generate a simple icon using sips from a generated image
# For now, create a placeholder - in production you'd want a proper .icns
cat > "$ICONSET_DIR/icon_512x512.png" <<'PYEOF'
# This is a placeholder - we'll use a generated icon
PYEOF

# Actually create a simple icon using Swift
cat > "$MACOS_DIR/create_icon.swift" <<'SWIFT'
import Cocoa
let size = 512
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let context = NSGraphicsContext.current!.cgContext
let rect = CGRect(x: 0, y: 0, width: size, height: size)
let colors = [NSColor.systemBlue.cgColor, NSColor.systemPurple.cgColor] as CFArray
let colorSpace = CGColorSpaceCreateDeviceRGB()
let gradient = CGGradient(colorsSpace: colorSpace, colors: colors, locations: [0, 1])!
context.drawLinearGradient(gradient, start: CGPoint(x: 0, y: 0), end: CGPoint(x: size, y: size), options: [])
let symbolConfig = NSFontSymbolConfiguration(pointSize: 256, weight: .medium)
let symbolImage = NSImage(systemSymbolName: "circle.dotted", accessibilityDescription: nil)?.withSymbolConfiguration(symbolConfig)
symbolImage?.draw(in: CGRect(x: size*0.2, y: size*0.2, width: size*0.6, height: size*0.6))
image.unlockFocus()
let tiffData = image.tiffRepresentation!
let bitmap = NSBitmapImageRep(data: tiffData)!
let pngData = bitmap.representation(using: .png, properties: [:])!
try pngData.write(to: URL(fileURLWithPath: "\(CommandLine.arguments[1])/icon_512x512.png"))
SWIFT

cd "$MACOS_DIR"
swift run -c "$CONFIGURATION" --package-path . create_icon.swift "$ICONSET_DIR" 2>/dev/null || {
    # Fallback: create a minimal valid PNG
    echo "Creating minimal icon..."
    python3 -c "
import struct, zlib
def png_chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
ihdr = struct.pack('>IIBBBBB', 512, 512, 8, 2, 0, 0, 0)
idat = zlib.compress(b''.join(b'\x00' + b'\x00\x00\xff' * 512 for _ in range(512)))
with open('$ICONSET_DIR/icon_512x512.png', 'wb') as f:
    f.write(b'\x89PNG\r\n\x1a\n')
    f.write(png_chunk(b'IHDR', ihdr))
    f.write(png_chunk(b'IDAT', idat))
    f.write(png_chunk(b'IEND', b''))
"
}

# Generate other icon sizes from the 512x512
for size in 16 32 64 128 256 512; do
    sips -z $size $size "$ICONSET_DIR/icon_512x512.png" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null 2>&1
done
for size in 32 64 128 256 512 1024; do
    sips -z $size $size "$ICONSET_DIR/icon_512x512.png" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null 2>&1
done

# Convert to icns
iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/AppIcon.icns" 2>/dev/null || true

# Update Info.plist to reference the icon
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" "$CONTENTS_DIR/Info.plist" 2>/dev/null || true

# Ad-hoc codesign
codesign --force --deep --sign - "$APP_BUNDLE" 2>/dev/null || true

echo "App bundle created at: $APP_BUNDLE"
echo ""
echo "To install: cp -r \"$APP_BUNDLE\" /Applications/"
echo "To run: open \"$APP_BUNDLE\""

# Cleanup temp files
rm -f "$MACOS_DIR/create_icon.swift"