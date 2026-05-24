#!/usr/bin/env bash
# flight-price-skill one-line installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/daghlny/flight-price-skill/main/install.sh | bash
#
# What it does:
#   1. Verifies python3 (>=3.10) and git are present.
#   2. Clones the repo to $HOME/.flight-price-skill (or pulls latest if it exists).
#   3. Creates a venv inside that directory.
#   4. pip-installs the package + playwright.
#   5. Downloads the Chromium binary playwright needs.
#   6. Drops a wrapper script into $HOME/.local/bin/flight-price.
#   7. If Claude Code is detected (~/.claude/), installs the skill into
#      ~/.claude/skills/flight-price/ automatically.
#   8. Tells you to add ~/.local/bin to PATH if it's not there.
#
# Idempotent — re-running upgrades to the latest main.

set -euo pipefail

REPO_URL="https://github.com/daghlny/flight-price-skill.git"
INSTALL_DIR="${FLIGHT_PRICE_HOME:-$HOME/.flight-price-skill}"
BIN_DIR="$HOME/.local/bin"
WRAPPER="$BIN_DIR/flight-price"

# --- helpers -----------------------------------------------------------------

c_red()   { printf '\033[31m%s\033[0m' "$1"; }
c_green() { printf '\033[32m%s\033[0m' "$1"; }
c_blue()  { printf '\033[34m%s\033[0m' "$1"; }
say()     { printf '%s %s\n' "$(c_blue "==>")" "$1"; }
warn()    { printf '%s %s\n' "$(c_red "!!!")" "$1" >&2; }
die()     { warn "$1"; exit 1; }

# --- preflight ---------------------------------------------------------------

say "Checking prerequisites"

command -v git >/dev/null 2>&1 || die "git is required but not found"
command -v python3 >/dev/null 2>&1 || die "python3 is required but not found"

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')
if [ "$PY_OK" != "1" ]; then
    die "python3 >= 3.10 required (found $PY_VER)"
fi
say "python3 $PY_VER  ✓"

# --- clone / update ----------------------------------------------------------

if [ -d "$INSTALL_DIR/.git" ]; then
    say "Updating existing checkout at $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --quiet origin
    git -C "$INSTALL_DIR" reset --quiet --hard origin/main
else
    say "Cloning $REPO_URL -> $INSTALL_DIR"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi

# --- venv + install ----------------------------------------------------------

VENV="$INSTALL_DIR/.venv"
if [ ! -d "$VENV" ]; then
    say "Creating venv at $VENV"
    python3 -m venv "$VENV"
fi

say "Installing package + playwright"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$INSTALL_DIR"

# --- playwright browser ------------------------------------------------------

say "Downloading Chromium for Playwright (~150MB, one-time)"
"$VENV/bin/playwright" install chromium

# --- wrapper -----------------------------------------------------------------

mkdir -p "$BIN_DIR"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/flight-price" "\$@"
EOF
chmod +x "$WRAPPER"
say "Wrapper installed -> $WRAPPER"

# --- skill install (Claude Code) --------------------------------------------

SKILL_INSTALLED=0
if [ -d "$HOME/.claude" ]; then
    SKILL_DST="$HOME/.claude/skills/flight-price"
    say "Detected Claude Code; installing skill -> $SKILL_DST"
    mkdir -p "$HOME/.claude/skills"
    rm -rf "$SKILL_DST"
    cp -r "$INSTALL_DIR/skill" "$SKILL_DST"
    SKILL_INSTALLED=1
else
    say "Claude Code not detected (~/.claude/ absent); skipping skill install"
fi

# --- PATH hint ---------------------------------------------------------------

if ! command -v flight-price >/dev/null 2>&1 || [ "$(command -v flight-price)" != "$WRAPPER" ]; then
    case ":$PATH:" in
        *":$BIN_DIR:"*)
            ;;
        *)
            warn "$BIN_DIR is not in your PATH"
            warn "Add this line to your ~/.zshrc (or ~/.bashrc):"
            warn '    export PATH="$HOME/.local/bin:$PATH"'
            warn "Then restart your shell."
            ;;
    esac
fi

# --- done --------------------------------------------------------------------

INSTALLED_VER="$("$WRAPPER" --version 2>/dev/null || echo 'unknown')"
echo
printf '%s flight-price installed: %s\n' "$(c_green '✓')" "$INSTALLED_VER"
printf '   Source:  %s\n' "$INSTALL_DIR"
printf '   Binary:  %s\n' "$WRAPPER"
if [ "$SKILL_INSTALLED" = "1" ]; then
    printf '   Skill:   %s\n' "$HOME/.claude/skills/flight-price"
    echo
    echo "Restart Claude Code, then try saying things like:"
    echo "  '帮我查端午请1天假去杭州怎么飞最便宜'"
    echo "  '比较 6 月每个周末北京飞东京的往返价'"
else
    echo
    echo "To use as an agent skill (Codex/Cursor/other), see:"
    echo "  $INSTALL_DIR/skill/README.md"
fi
echo
echo "Or use directly as a CLI:"
echo "  flight-price BJS SHA --from \$(date -v+7d +%Y-%m-%d 2>/dev/null || date -d '+7 days' +%Y-%m-%d)"
echo "  flight-price man"
