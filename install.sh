#!/bin/sh
# zmk-layer-hud installer.
#
#   curl -fsSL https://raw.githubusercontent.com/rafaelromao/zmk-layer-hud/main/install.sh | sh
#
# It puts the tree in ~/.local/share/zmk-layer-hud and the command in ~/.local/bin, then hands
# over to `zmk-layer-hud setup` for the machine itself. Nothing here runs as root: setup prints
# every privileged step and asks first, and under a pipe -- where there is no one to ask -- it
# prints them and stops. $ZMKHUD_HOME moves the tree, $ZMKHUD_REF picks a branch.
set -eu

REPO=rafaelromao/zmk-layer-hud
REF=${ZMKHUD_REF:-main}
HOME_DIR=${ZMKHUD_HOME:-$HOME/.local/share/zmk-layer-hud}

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) echo "zmk-layer-hud: no host for $(uname -s); macOS and Linux (Hyprland) only" >&2; exit 1 ;;
esac

command -v tar >/dev/null 2>&1 || { echo "zmk-layer-hud: tar is needed" >&2; exit 1; }
if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -qO "$2" "$1"; }
else
  echo "zmk-layer-hud: curl or wget is needed" >&2; exit 1
fi

# A clone is updated with `git pull`; overwriting one would throw away whatever is uncommitted.
if [ -d "$HOME_DIR/.git" ]; then
  echo "zmk-layer-hud: $HOME_DIR is a git clone -- update it with: git -C $HOME_DIR pull" >&2
  exit 1
fi

TMP=$(mktemp -d)
mkdir -p "$(dirname "$HOME_DIR")"
# Staged beside the destination, not in $TMP: /tmp is often another filesystem, and the whole
# point of staging is that the swap is two renames within one directory. A half-finished copy
# across devices is exactly how someone loses the venv that was moved into it.
NEW="$HOME_DIR.new"
rm -rf "$NEW"
# shellcheck disable=SC2064
trap "rm -rf '$TMP' '$NEW'" EXIT INT TERM

echo "==> fetching $REPO@$REF"
fetch "https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF" "$TMP/tree.tar.gz"
mkdir -p "$NEW"
tar xzf "$TMP/tree.tar.gz" -C "$NEW" --strip-components=1

# The venv is expensive to rebuild and holds nothing the tree owns, so it survives a reinstall.
if [ -d "$HOME_DIR/.venv" ]; then
  mv "$HOME_DIR/.venv" "$NEW/.venv"
  echo "    kept the existing venv"
fi

if [ -d "$HOME_DIR" ]; then
  rm -rf "$HOME_DIR.old"
  mv "$HOME_DIR" "$HOME_DIR.old"
fi
mv "$NEW" "$HOME_DIR"
rm -rf "$HOME_DIR.old"
# A tarball carries whatever mode the archive held; the command is useless without the bit.
chmod 755 "$HOME_DIR/bin/zmk-layer-hud"
echo "    tree at $HOME_DIR"

# setup owns the symlink, the PATH advice and the machine itself, so there is one implementation
# of each. Under `curl | sh` stdin is the pipe, so reopen the terminal where there is one -- that
# is what lets setup ask before anything privileged instead of silently skipping it.
echo
if [ -r /dev/tty ]; then
  "$HOME_DIR/bin/zmk-layer-hud" setup --link </dev/tty
else
  "$HOME_DIR/bin/zmk-layer-hud" setup --link
fi
