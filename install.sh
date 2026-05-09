#!/usr/bin/env bash
# Install nautilus-git-status Nautilus extension and emblem icons.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXT_DIR="$HOME/.local/share/nautilus-python/extensions"
EMBLEM_DIR="$HOME/.local/share/icons/hicolor/scalable/emblems"
CONFIG_DIR="$HOME/.config/nautilus-git-status"
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
#    github-remote indicator) so the user's icon dir matches current state.
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
# Edit and save — Nautilus picks up the change without a restart.

primary   = iraum
secondary = x42i
tertiary  = iraum-oracle
EOF
  echo "seeded config      -> $CONFIG_FILE"
else
  echo "kept config        -> $CONFIG_FILE (already exists)"
fi

# 5. Refresh GTK icon cache so Nautilus can find the new emblems.
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" || true
fi

# 6. Restart Nautilus so the extension loads.
if pgrep -x nautilus >/dev/null 2>&1; then
  echo "restarting nautilus..."
  nautilus -q || true
  sleep 1
  (nohup nautilus >/dev/null 2>&1 &) || true
fi

echo "done. Open a folder containing git repos to see emblems."
