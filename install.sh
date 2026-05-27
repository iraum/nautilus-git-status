#!/usr/bin/env bash
# Install nautilus-git-status Nautilus extension and emblem icons.
#
# Two modes:
#   ./install.sh            per-user install into $HOME (default)
#   sudo ./install.sh       system-wide install into /usr/share + /etc,
#   ./install.sh --system   shared by every account on the machine
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Decide mode: --system flag or running as root => system-wide.
SYSTEM=0
for arg in "$@"; do
  case "$arg" in
    --system) SYSTEM=1 ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done
if [[ $EUID -eq 0 ]]; then
  SYSTEM=1
fi

if [[ $SYSTEM -eq 1 ]]; then
  if [[ $EUID -ne 0 ]]; then
    echo "system-wide install needs root. Re-run with: sudo $0 --system"
    exit 1
  fi
  EXT_DIR="/usr/share/nautilus-python/extensions"
  EMBLEM_ROOT="/usr/share/icons/hicolor"
  CONFIG_DIR="/etc/nautilus-git-status"
else
  EXT_DIR="$HOME/.local/share/nautilus-python/extensions"
  EMBLEM_ROOT="$HOME/.local/share/icons/hicolor"
  CONFIG_DIR="$HOME/.config/nautilus-git-status"
fi
EMBLEM_DIR="$EMBLEM_ROOT/scalable/emblems"
CONFIG_FILE="$CONFIG_DIR/profiles.conf"

# 1. nautilus-python (binding) must be installed system-wide.
if ! rpm -q nautilus-python >/dev/null 2>&1; then
  echo "nautilus-python is not installed. Install with:"
  echo "  sudo dnf install -y nautilus-python"
  echo "(Requires the ol9_developer_EPEL repo enabled — see project README.)"
  exit 1
fi

# 2. Drop the extension in place. Remove the old git-emblems.py if it's
#    still around from a pre-split install — Nautilus would otherwise load
#    both and they'd fight over the same Provider role.
mkdir -p "$EXT_DIR"
rm -f "$EXT_DIR/git-emblems.py"
cp -f "$SCRIPT_DIR/nautilus-git-status.py" "$EXT_DIR/nautilus-git-status.py"
echo "installed extension -> $EXT_DIR/nautilus-git-status.py"

# 3. Install emblem icons. Remove emblems left over from earlier versions
#    (single-dot status icons before ownership tiers, and the unrelated
#    github-remote indicator) so the icon dir matches current state.
mkdir -p "$EMBLEM_DIR"
rm -f "$EMBLEM_DIR/emblem-github-remote.svg" \
      "$EMBLEM_DIR/emblem-git-ahead.svg" \
      "$EMBLEM_DIR/emblem-git-behind.svg" \
      "$EMBLEM_DIR/emblem-git-clean.svg" \
      "$EMBLEM_DIR/emblem-git-dirty.svg"
cp -f "$SCRIPT_DIR/icons/"emblem-*.svg "$EMBLEM_DIR/"
echo "installed emblems  -> $EMBLEM_DIR/"

# 4. Seed the ownership config the first time only — never clobber an
#    existing file the user may have edited.
mkdir -p "$CONFIG_DIR"
if [[ ! -e "$CONFIG_FILE" ]]; then
  cat > "$CONFIG_FILE" <<'EOF'
# nautilus-git-status ownership profiles
#
# Each line maps a tier to a comma-separated list of identifiers.
# Identifiers are matched case-insensitively against, in order:
#   1. the owner slug parsed from `git remote get-url origin`
#      (e.g. "iraum" for github.com:iraum/foo.git)
#   2. `git config user.name`     (only when origin is missing)
#   3. `git config user.email`    (last-resort fallback)
#
# A repo whose identifier doesn't match any tier here renders as
# "external" (plain status disk, no inner tier dot).
#
# Config resolution is system -> user: this shared base lives in
# /etc; a per-account ~/.config/nautilus-git-status/profiles.conf, if
# present, overlays it per-identifier. Edit and save — Nautilus picks
# up the change without a restart.

primary   = iraum
secondary = x42i
tertiary  = iraum-oracle
# opc is currently 'external' (plain disk, no inner dot). To give opc a
# tier, add it to one of the lines above (e.g. tertiary = iraum-oracle, opc).
#
# For repos with NO origin remote, tiering falls back to git's
# user.name / user.email. If an account's configured user.name/email
# differs from its owner slug, add those values to the matching tier
# line so local-only repos tier correctly.
EOF
  echo "seeded config      -> $CONFIG_FILE"
else
  echo "kept config        -> $CONFIG_FILE (already exists)"
fi

# 5. Refresh GTK icon cache so Nautilus can find the new emblems.
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$EMBLEM_ROOT" || true
fi

# 6. Make the extension live.
if [[ $SYSTEM -eq 1 ]]; then
  # We can't (and shouldn't) restart other users' Nautilus sessions from a
  # root install. Each logged-in user reloads to pick up the extension.
  echo "system-wide install done."
  echo "Each logged-in user should reload Nautilus to load the extension:"
  echo "  nautilus -q        # usually enough"
  echo "  # if the surface doesn't appear (gapplication-service keeps the"
  echo "  # old process alive):  pkill -u \$USER nautilus && sleep 1 && nautilus &"
else
  if pgrep -x nautilus >/dev/null 2>&1; then
    echo "restarting nautilus..."
    nautilus -q || true
    sleep 1
    (nohup nautilus >/dev/null 2>&1 &) || true
  fi
  echo "done. Open a folder containing git repos to see emblems."
fi
