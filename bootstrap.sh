#!/bin/zsh
# DANDORI in one line, on a Mac that has never seen it:
#
#   curl -fsSL https://raw.githubusercontent.com/KonichiValue/DANDORI/main/bootstrap.sh | zsh
#
# or, once it is there, ~/Projects/DANDORI/bootstrap.sh to update it.
#
# It fetches DANDORI into ~/Projects/DANDORI (or pulls it if it is already there),
# then runs ./install.sh, which builds the app and opens the setup page. Every
# step after that is a button on that page. Safe to run again: it is also how
# DANDORI updates itself.
#
# DANDORI_FROM names another copy of the repo, DANDORI_DIR another folder.

set -euo pipefail

FROM="${DANDORI_FROM:-https://github.com/KonichiValue/DANDORI.git}"
DIR="${DANDORI_DIR:-$HOME/Projects/DANDORI}"

# git and python3 both come with Apple's command line tools. Without them the
# first call to either opens Apple's installer, so ask for it plainly instead.
if ! xcode-select -p >/dev/null 2>&1; then
  print -r -- "DANDORI needs Apple's command line tools (git and python3)."
  print -r -- "An installer window is opening. When it finishes, run this line again."
  xcode-select --install 2>/dev/null || true
  exit 1
fi

if [[ -d "$DIR/.git" ]]; then
  print -r -- "Updating DANDORI in $DIR"
  git -C "$DIR" pull --ff-only
elif [[ -d "$DIR/core" && -x "$DIR/install.sh" ]]; then
  print -r -- "Reinstalling DANDORI in $DIR"
else
  [[ -e "$DIR" ]] && { print -u2 "$DIR exists and is not DANDORI. Move it, or set DANDORI_DIR."; exit 1 }
  mkdir -p "${DIR:h}"
  print -r -- "Downloading DANDORI into $DIR"
  if ! git clone --depth 1 "$FROM" "$DIR"; then
    if [[ -z "${DANDORI_FROM:-}" ]]; then
      if command -v gh >/dev/null 2>&1 && gh repo clone KonichiValue/DANDORI "$DIR" -- --depth 1; then
        :
      else
        print -r -- "SSH clone failed; trying HTTPS..."
        git clone --depth 1 "https://github.com/KonichiValue/DANDORI.git" "$DIR"
      fi
    else
      exit 1
    fi
  fi
fi

exec "$DIR/install.sh"
