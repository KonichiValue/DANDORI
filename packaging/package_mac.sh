#!/bin/zsh
# Package DANDORI as a standalone, double-clickable macOS application:
#
#   ./packaging/package_mac.sh
#   DANDORI package
#
# Produces in dist/:
#   - DANDORI.app              Self-bootstrapping macOS app bundle (contains core runtime)
#   - DANDORI-<ver>.dmg        Drag-to-Applications disk image for other Mac users
#   - DANDORI-<ver>.pkg        Standard macOS Installer package (.pkg)
#   - DANDORI-<ver>.zip        Compressed app bundle for Slack or GitHub Releases
#
# Privacy guarantee: only git-tracked core files are staged into the app bundle.
# Client workspaces (`clients/<client>/`), configs, profiles, boards, docs and
# logs are never included, and this script verifies their absence before building.

set -euo pipefail

REPO="${0:A:h:h}"
cd "$REPO"
export PYTHONPATH="$REPO/core${PYTHONPATH:+:$PYTHONPATH}"

VER="$(python3 -c 'import settings; print(settings.version())')"
VER_LABEL="$(python3 -c 'import settings; print(settings.version_label())')"
APP_NAME="DANDORI"
DIST="$REPO/dist"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/dandori-pkg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT INT TERM

print -r -- "=== Packaging $APP_NAME $VER_LABEL ==="

# 1. Run check.py to ensure no personal config has leaked into tracked files.
print -r -- "1. Running pre-package checks (./core/check.py --quiet)..."
python3 "$REPO/core/check.py" --quiet

# 2. Build icons if needed.
print -r -- "2. Building application icons..."
python3 "$REPO/packaging/make_icon.py" "$REPO/packaging/icon.png" >/dev/null
ICONSET="$STAGE/icon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 64 128 256 512 1024; do
  sips -z $s $s "$REPO/packaging/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
done
for s in 16 32 128 256 512; do
  cp "$ICONSET/icon_$((s * 2))x$((s * 2)).png" "$ICONSET/icon_${s}x${s}@2x.png" 2>/dev/null || true
done
iconutil -c icns "$ICONSET" -o "$REPO/packaging/icon.icns" 2>/dev/null || true
for s in 192 512; do
  sips -z $s $s "$REPO/packaging/icon.png" --out "$REPO/packaging/icon-$s.png" >/dev/null 2>&1
done

# 3. Assemble the self-contained DANDORI.app bundle.
print -r -- "3. Assembling standalone $APP_NAME.app..."
mkdir -p "$DIST"
APP_BUNDLE="$DIST/$APP_NAME.app"
rm -rf "$APP_BUNDLE"

# Compile the macOS launcher applet so LaunchServices opens it natively.
osacompile -o "$APP_BUNDLE" -e '
set bundlePath to POSIX path of (path to me)
set launcherPath to bundlePath & "Contents/Resources/launch_dandori.sh"
do shell script quoted form of launcherPath
'

[[ -f "$REPO/packaging/icon.icns" ]] && cp "$REPO/packaging/icon.icns" "$APP_BUNDLE/Contents/Resources/applet.icns"

# Stage only git-tracked files into Contents/Resources/runtime/ so no personal
# client workspace or state can ever be bundled.
RUNTIME="$APP_BUNDLE/Contents/Resources/runtime"
mkdir -p "$RUNTIME"
while IFS= read -r rel; do
  [[ -z "$rel" ]] && continue
  mkdir -p "$RUNTIME/${rel:h}"
  cp -R "$REPO/$rel" "$RUNTIME/$rel"
done < <(git -C "$REPO" ls-files)

mkdir -p "$RUNTIME/clients" "$RUNTIME/packaging"
[[ -f "$REPO/packaging/icon.png" ]] && cp "$REPO/packaging/icon.png" "$RUNTIME/packaging/icon.png"
[[ -f "$REPO/packaging/icon.icns" ]] && cp "$REPO/packaging/icon.icns" "$RUNTIME/packaging/icon.icns"

# Verify zero personal files exist inside the staged bundle.
for forbidden in config.json board.json asks.json profile.md; do
  found="$(find "$RUNTIME" -name "$forbidden" 2>/dev/null || true)"
  if [[ -n "$found" ]]; then
    print -u2 "ABORT: personal file found in staged bundle: $found"
    exit 1
  fi
done

# Write the first-run / open launcher inside the bundle.
print -r -- "$VER" >"$RUNTIME/core/.bundle-version"
cat >"$APP_BUNDLE/Contents/Resources/launch_dandori.sh" <<'LAUNCH'
#!/bin/zsh
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

BUNDLE_RES="${0:A:h}"
RUNTIME="$BUNDLE_RES/runtime"
TARGET="${DANDORI_DIR:-$HOME/Projects/DANDORI}"

if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "DANDORI needs Apple Command Line Developer Tools (Python 3). Click OK to open the Apple installer, then open DANDORI again when it finishes." buttons {"OK"} default button "OK" with title "DANDORI Setup"' >/dev/null 2>&1 || true
  xcode-select --install >/dev/null 2>&1 || true
  exit 0
fi

# First launch on a new Mac (or upgrade from a newer .dmg/.pkg): unpack or sync
# the embedded core runtime into ~/Projects/DANDORI without touching clients/.
if [[ ! -d "$TARGET/core" ]]; then
  mkdir -p "${TARGET:h}"
  cp -R "$RUNTIME" "$TARGET"
  chmod +x "$TARGET/install.sh" "$TARGET/bootstrap.sh" "$TARGET/bin/DANDORI" "$TARGET/core/"*.py "$TARGET/core/scripts/"*.sh
  exec "$TARGET/install.sh"
elif [[ -f "$RUNTIME/core/.bundle-version" ]] && [[ "$(cat "$RUNTIME/core/.bundle-version" 2>/dev/null)" != "$(cat "$TARGET/core/.bundle-version" 2>/dev/null)" ]]; then
  for item in core bin packaging tests install.sh bootstrap.sh AGENTS.md GUIDE.md README.md; do
    [[ -e "$RUNTIME/$item" ]] && cp -R "$RUNTIME/$item" "$TARGET/"
  done
  chmod +x "$TARGET/install.sh" "$TARGET/bootstrap.sh" "$TARGET/bin/DANDORI" "$TARGET/core/"*.py "$TARGET/core/scripts/"*.sh
  DANDORI_NO_OPEN=1 "$TARGET/install.sh" >/dev/null 2>&1 || true
fi

exec "$TARGET/bin/DANDORI" open
LAUNCH
chmod +x "$APP_BUNDLE/Contents/Resources/launch_dandori.sh"

# Ad-hoc sign the bundle so macOS Gatekeeper / LaunchServices accepts it cleanly.
codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
touch "$APP_BUNDLE"

# 4. Build the drag-and-drop Disk Image (.dmg)
DMG_PATH="$DIST/$APP_NAME-$VER.dmg"
DMG_DIR="$STAGE/dmg"
mkdir -p "$DMG_DIR"
cp -R "$APP_BUNDLE" "$DMG_DIR/$APP_NAME.app"
ln -s /Applications "$DMG_DIR/Applications"
rm -f "$DMG_PATH"
print -r -- "4. Building disk image: ${DMG_PATH#$REPO/}..."
hdiutil create -volname "$APP_NAME $VER" -srcfolder "$DMG_DIR" -ov -format UDZO "$DMG_PATH" >/dev/null

# 5. Build the macOS Installer Package (.pkg)
PKG_PATH="$DIST/$APP_NAME-$VER.pkg"
PKG_ROOT="$STAGE/pkgroot"
PKG_SCRIPTS="$STAGE/pkgscripts"
mkdir -p "$PKG_ROOT/Applications" "$PKG_SCRIPTS"
cp -R "$APP_BUNDLE" "$PKG_ROOT/Applications/$APP_NAME.app"
cat >"$PKG_SCRIPTS/postinstall" <<'POST'
#!/bin/zsh
USER_NAME="$(stat -f '%Su' /dev/console 2>/dev/null || true)"
if [[ -n "$USER_NAME" && "$USER_NAME" != "root" ]]; then
  USER_HOME="$(dscl . -read "/Users/$USER_NAME" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  if [[ -n "$USER_HOME" && -d "/Applications/DANDORI.app/Contents/Resources/runtime" ]]; then
    TARGET="$USER_HOME/Projects/DANDORI"
    if [[ ! -d "$TARGET/core" ]]; then
      su -l "$USER_NAME" -c "mkdir -p '$USER_HOME/Projects' && cp -R '/Applications/DANDORI.app/Contents/Resources/runtime' '$TARGET' && '$TARGET/install.sh'" >/dev/null 2>&1 || true
    fi
  fi
fi
exit 0
POST
chmod +x "$PKG_SCRIPTS/postinstall"
rm -f "$PKG_PATH"
print -r -- "5. Building macOS installer package: ${PKG_PATH#$REPO/}..."
pkgbuild --root "$PKG_ROOT" --scripts "$PKG_SCRIPTS" --identifier "com.DANDORI.app" --version "$VER" --install-location "/" "$PKG_PATH" >/dev/null

# 6. Build the compressed .zip archive
ZIP_PATH="$DIST/$APP_NAME-$VER.zip"
rm -f "$ZIP_PATH"
print -r -- "6. Building zip archive: ${ZIP_PATH#$REPO/}..."
ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ZIP_PATH"

print -r -- ""
print -r -- "Done! Distributable packages in dist/:"
print -r -- "  • dist/$APP_NAME-$VER.dmg   (drag DANDORI.app to Applications)"
print -r -- "  • dist/$APP_NAME-$VER.pkg   (double-click macOS Installer wizard)"
print -r -- "  • dist/$APP_NAME-$VER.zip   (ready to drop in Slack or GitHub Releases)"
print -r -- "  • dist/$APP_NAME.app        (self-bootstrapping application bundle)"
