#!/bin/zsh
# Put the list one click away: a Dock app, and `DANDORI` in the terminal.
#
#   ./install.sh
#
# Both point at this checkout, so nothing is copied and nothing goes stale.
# Run it again after pulling; it is safe to repeat.
#
# On a fresh copy it needs no answers first: it installs with the defaults and
# opens the setup page, where every other step is a button. DANDORI_NO_OPEN=1
# skips opening it, which is how the setup page itself runs this.

set -euo pipefail

DIR="${0:A:h}"
cd "$DIR"
BIN="$HOME/.local/bin"
USER_PATH=":$PATH:"
export PATH="$BIN:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$DIR/core${PYTHONPATH:+:$PYTHONPATH}"

command -v python3 >/dev/null 2>&1 || {
  print -r -- "python3 is missing. Run: xcode-select --install, then this again."
  exit 1
}

NAME="$(python3 -c 'import settings; print(settings.desk_name())')"
VER="$(python3 -c 'import settings; print(settings.version_label())')"
CMD="$(python3 -c 'import settings; print(settings.command())')"
PORT="$(python3 -c 'import settings; print(settings.port())')"
LOGS_DIR="$(python3 -c 'import settings; print(settings.LOGS_DIR)')"
CFG_PATH="$(python3 -c 'import settings; print(settings.CONFIG)')"
APP="$HOME/Applications/$NAME.app"

mkdir -p "$BIN" "$HOME/Applications" "$LOGS_DIR"

chmod +x "$DIR/bin/DANDORI" "$DIR/core/"*.py "$DIR/core/scripts/"*.sh "$DIR/packaging/make_icon.py"
[[ -f "$DIR/packaging/package_mac.sh" ]] && chmod +x "$DIR/packaging/package_mac.sh"

# `DANDORI` always, and the owner's own word for it as well when they chose one.
typeset -U words=(DANDORI "$CMD")
for word in $words; do
  [[ "$word" == */* || -z "$word" ]] && continue
  ln -sf "$DIR/bin/DANDORI" "$BIN/$word"
  print -r -- "linked $BIN/$word"
done
[[ "$USER_PATH" == *":$BIN:"* ]] || print -r -- "add $BIN to your PATH (echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc)"

# Icon: drawn here rather than shipped as a binary blob in the repo.
python3 "$DIR/packaging/make_icon.py" "$DIR/packaging/icon.png" >/dev/null
ICONSET="$(mktemp -d)/icon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 64 128 256 512 1024; do
  sips -z $s $s "$DIR/packaging/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
done
# Retina names macOS expects alongside the plain ones.
for s in 16 32 128 256 512; do
  cp "$ICONSET/icon_$((s * 2))x$((s * 2)).png" "$ICONSET/icon_${s}x${s}@2x.png" 2>/dev/null || true
done
iconutil -c icns "$ICONSET" -o "$DIR/packaging/icon.icns" 2>/dev/null \
  || print -r -- "could not build the icns, the app will use the generic icon"

# The two sizes the web manifest points at, so Chrome has an icon to give the
# installed app instead of falling back to its own logo.
for s in 192 512; do
  sips -z $s $s "$DIR/packaging/icon.png" --out "$DIR/packaging/icon-$s.png" >/dev/null 2>&1
done

# osacompile rather than a hand-rolled bundle: LaunchServices refuses to open a
# bare script bundle on this macOS, and this one comes out signed and valid.
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"'$DIR/bin/DANDORI' open\""
[[ -f "$DIR/packaging/icon.icns" ]] && cp "$DIR/packaging/icon.icns" "$APP/Contents/Resources/applet.icns"
touch "$APP"
print -r -- "built $APP ($VER)"

# Keep the page server up from login, so an installed web app always finds it.
pkill -f "serve.py --port $PORT" 2>/dev/null || true
sleep 1
python3 "$DIR/core/launchd.py" on serve \
  || print -r -- "could not load the page server agent, start it with: $CMD open"
print -r -- "page server running on http://127.0.0.1:$PORT/"

# The fold-in of the main standup's notes, as it ends on its days. Not before
# the setup questions are answered: until then there is no standup to fold in.
if [[ -f "$CFG_PATH" ]]; then
  python3 "$DIR/core/launchd.py" on post || print -r -- "could not load the post-standup agent"
fi

if [[ -z "${DANDORI_NO_OPEN:-}" ]]; then
  print -r -- ""
  print -r -- "Opening $NAME."
  "$DIR/bin/DANDORI" open || true
fi

print -r -- ""
print -r -- "  $CMD            open the page and see where you are"
print -r -- "  $CMD refresh    go and see what moved (the first one builds the board)"
print -r -- "  $CMD 4          finish item 4"
print -r -- "  $CMD scripts    list client workflow scripts"
print -r -- "  $CMD package    build distributable macOS .dmg / .pkg / .zip"
print -r -- "  $CMD morning    sweep every weekday morning (optional, spends tokens)"
