#!/usr/bin/env bash
# The real HUD under a real Wayland compositor (Hyprland, or niri: E2E_COMPOSITOR), in a disposable Arch
# container on a GitHub-hosted runner (.github/workflows/e2e.yml). Nothing here is for a machine you care
# about: it installs packages and creates a user.
#
# A compositor cannot start without a DRM device, and a hosted runner has no GPU. The runner's host loads
# vkms (a virtual KMS device), and the container is --privileged so it sees /dev/dri. Everything the
# container does:
#
#   run-in-arch.sh            as root: the packages, the vkms card, a user; then the session below, as that user
#   run-in-arch.sh session    as that user, inside dbus-run-session: the compositor on vkms, the venv, e2e/driver.py
#
# Hyprland's maintainers do not support headless CI; this follows what worked for others (the recipe is
# in e2e/linux/hyprland.lua's header) and is checked by each run, which is the point of it. niri is the same
# idea by the same route -- its DRM backend on vkms through libseat's noop backend, which it takes when none
# of WAYLAND_DISPLAY, WAYLAND_SOCKET and DISPLAY is set -- and is not documented as a CI use either.
set -uo pipefail

OUT="${E2E_OUT:-/work/out}"
REPO_RO="${E2E_REPO:-/work/repo}"
COMPOSITOR="${E2E_COMPOSITOR:-hyprland}"
USER_NAME=tester
HOME_DIR="/home/$USER_NAME"
REPO="$HOME_DIR/zmk-layer-hud"

say() { printf '\n== %s\n' "$*"; }

# ----- the session, as the user -----

# Sets CPID and exports what the driver and the HUD look for; returns non-zero, with the reason said, when
# the compositor did not come up.
start_hyprland() {
  mkdir -p "$HOME/.config/hypr"
  cp e2e/linux/hyprland.lua "$HOME/.config/hypr/hyprland.lua"
  Hyprland >"$OUT/hyprland-stdout.log" 2>&1 &
  CPID=$!
  local sock=""
  for _ in $(seq 1 120); do
    sock=$(find "$XDG_RUNTIME_DIR/hypr" -name .socket.sock 2>/dev/null | head -n1)
    [ -n "$sock" ] && break
    kill -0 "$CPID" 2>/dev/null || break
    sleep 0.5
  done
  if [ -z "$sock" ]; then
    echo "Hyprland made no IPC socket (alive: $(kill -0 "$CPID" 2>/dev/null && echo yes || echo no))"
    tail -n 60 "$OUT/hyprland-stdout.log"
    return 3
  fi
  HYPRLAND_INSTANCE_SIGNATURE=$(basename "$(dirname "$sock")")
  export HYPRLAND_INSTANCE_SIGNATURE
}

start_niri() {
  mkdir -p "$HOME/.config/niri"
  cp e2e/linux/niri.kdl "$HOME/.config/niri/config.kdl"
  # niri takes its DRM backend when none of these is set, and not `--session`, which is for a TTY login.
  env -u WAYLAND_DISPLAY -u WAYLAND_SOCKET -u DISPLAY RUST_BACKTRACE=1 niri >"$OUT/niri-stdout.log" 2>&1 &
  CPID=$!
  local sock=""
  for _ in $(seq 1 120); do
    sock=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'niri.*.sock' 2>/dev/null | head -n1)
    [ -n "$sock" ] && break
    kill -0 "$CPID" 2>/dev/null || break
    sleep 0.5
  done
  if [ -z "$sock" ]; then
    echo "niri made no IPC socket (alive: $(kill -0 "$CPID" 2>/dev/null && echo yes || echo no))"
    tail -n 60 "$OUT/niri-stdout.log"
    return 3
  fi
  export NIRI_SOCKET="$sock"
}

session() {
  cd "$REPO" || exit 2
  export LIBGL_ALWAYS_SOFTWARE=1 LIBSEAT_BACKEND=noop GDK_BACKEND=wayland PYTHONUNBUFFERED=1
  if [ -n "${VKMS_CARD:-}" ]; then
    export AQ_DRM_DEVICES="$VKMS_CARD"        # Hyprland's (aquamarine's) choice of card; niri takes what udev lists
  fi

  say "the venv, as 'zmk-layer-hud setup' makes it"
  bin/zmk-layer-hud setup --venv-only >"$OUT/venv.log" 2>&1 || { tail -n 30 "$OUT/venv.log"; echo venv-failed >"$OUT/status.txt"; return 2; }

  say "$COMPOSITOR on ${VKMS_CARD:-no card}"
  CPID=""
  if [ "$COMPOSITOR" = niri ]; then start_niri; else start_hyprland; fi || { echo "$COMPOSITOR-failed" >"$OUT/status.txt"; return 3; }
  local wl=""
  for _ in $(seq 1 40); do
    wl=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'wayland-*' | head -n1)
    [ -n "$wl" ] && break
    sleep 0.5
  done
  export WAYLAND_DISPLAY="${wl##*/}"
  echo "$COMPOSITOR is up: WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
  case "$COMPOSITOR" in
    hyprland)
      hyprctl version -j >"$OUT/hyprctl-version-before.json" 2>&1
      hyprctl monitors -j >"$OUT/hyprctl-monitors-before.json" 2>&1
      hyprctl configerrors >"$OUT/hyprctl-configerrors-before.txt" 2>&1 ;;
    niri)
      niri --version >"$OUT/niri-version-before.txt" 2>&1
      niri msg --json outputs >"$OUT/niri-outputs-before.json" 2>&1 ;;
  esac

  say "the driver"
  CI=true python3 e2e/driver.py --artifacts "$OUT/driver" --probe "$COMPOSITOR"
  local rc=$?

  say "the compositor's own log"
  case "$COMPOSITOR" in
    hyprland) hyprctl dispatch exit >/dev/null 2>&1 || kill "$CPID" 2>/dev/null ;;
    niri)     niri msg action quit --skip-confirmation >/dev/null 2>&1 || kill "$CPID" 2>/dev/null ;;
  esac
  wait "$CPID" 2>/dev/null
  find "$XDG_RUNTIME_DIR/hypr" "$HOME/.cache/hyprland" -name '*.log' 2>/dev/null | while read -r f; do
    cp "$f" "$OUT/hyprland-$(basename "$(dirname "$f")")-$(basename "$f")" 2>/dev/null
  done
  return $rc
}

if [ "${1:-}" = session ]; then
  session
  exit $?
fi

# ----- the container, as root -----

root() {
  mkdir -p "$OUT"
  uname -a | tee "$OUT/uname.txt"

  say "packages for $COMPOSITOR"
  case "$COMPOSITOR" in
    hyprland|niri) ;;
    *) echo "E2E_COMPOSITOR is hyprland or niri, not '$COMPOSITOR'"; return 2 ;;
  esac
  local ok=1
  for attempt in 1 2 3; do
    echo "pacman attempt $attempt" >>"$OUT/pacman.log"
    if pacman -Syu --noconfirm --needed \
         python python-pip git make gcc procps-ng \
         "$COMPOSITOR" mesa seatd dbus gtk3 webkit2gtk-4.1 gtk-layer-shell python-gobject python-cairo grim ttf-dejavu \
         >>"$OUT/pacman.log" 2>&1; then ok=0; break; fi
    sleep 10
  done
  if [ "$ok" != 0 ]; then tail -n 20 "$OUT/pacman.log"; echo pacman-failed >"$OUT/status.txt"; return 2; fi
  pacman -Q "$COMPOSITOR" mesa webkit2gtk-4.1 gtk3 gtk-layer-shell python python-gobject seatd grim 2>&1 | tee "$OUT/packages.txt"

  say "the virtual KMS device"
  # Its index varies, and kernel 7 registers vkms as a faux device (driver faux_driver), so the card is
  # found by what its device is called.
  local card drv dev vkms=""
  for card in /sys/class/drm/card[0-9]; do
    [ -e "$card" ] || continue
    drv=$(basename "$(readlink -f "$card/device/driver" 2>/dev/null)")
    dev=$(basename "$(readlink -f "$card/device" 2>/dev/null)")
    echo "$(basename "$card") driver=$drv device=$dev" | tee -a "$OUT/drm.txt"
    if [ "$drv" = vkms ] || [ "$dev" = vkms ]; then vkms="/dev/dri/$(basename "$card")"; fi
  done
  # shellcheck disable=SC2012
  ls -l /dev/dri | tee -a "$OUT/drm.txt"
  if [ -z "$vkms" ]; then
    echo "no vkms card: the compositor has nothing to run on (the runner's host did not load the module?)" | tee -a "$OUT/drm.txt"
    echo no-vkms >"$OUT/status.txt"
    return 4
  fi
  echo "vkms card: $vkms" | tee -a "$OUT/drm.txt"
  # The nodes carry the host's video GID, which this container's video group does not have.
  chmod 0666 "$vkms"

  say "a user (Hyprland refuses root) and a copy of the repo it owns"
  useradd -m -s /bin/bash -G video "$USER_NAME"
  cp -a "$REPO_RO" "$REPO"
  install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" /tmp/xdg-runtime
  chown -R "$USER_NAME:$USER_NAME" "$REPO" "$OUT"

  say "the session"
  runuser -u "$USER_NAME" -- env -i \
    PATH=/usr/local/bin:/usr/bin HOME="$HOME_DIR" USER="$USER_NAME" LOGNAME="$USER_NAME" SHELL=/bin/bash \
    LANG=C.UTF-8 XDG_RUNTIME_DIR=/tmp/xdg-runtime VKMS_CARD="$vkms" E2E_OUT="$OUT" E2E_COMPOSITOR="$COMPOSITOR" \
    dbus-run-session -- bash "$REPO/e2e/linux/run-in-arch.sh" session
}

root
rc=$?
echo "$rc" >"$OUT/exit-code.txt"
# What the runner's own user uploads has to be its to read.
chown -R "${HOST_UID:-1001}:${HOST_GID:-1001}" "$OUT" 2>/dev/null
chmod -R a+rX "$OUT" 2>/dev/null
exit $rc
