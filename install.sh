#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  PG-Backup — One-command Installer
# ══════════════════════════════════════════════════════════════════════════════
#  Developer  : CIAUB
#  GitHub     : https://github.com/CIAUB/PG-Backup
#  License    : CC BY-NC-ND 4.0
#
#  What it does (and nothing else):
#    1. downloads pg_backup.py from an immutable release TAG (not a moving branch)
#    2. verifies its SHA-256 against pg_backup.py.sha256 (and optionally against a
#       hash YOU pass in out-of-band)
#    3. syntax-checks it, then installs it atomically to /usr/local/bin/PG-Backup
#
#  Usage:
#    bash install.sh                      # install the release this installer ships with
#    bash install.sh v5.0.0               # pin another release tag
#    bash install.sh --sha256 <hex>       # additionally require this exact hash
#    bash install.sh --offline            # use pg_backup.py next to install.sh
#    bash install.sh --main               # NOT RECOMMENDED: track the main branch
#
#  Integrity note: the .sha256 file is fetched from the same GitHub repo, so it
#  protects against corruption / partial downloads / CDN mix-ups, but not against
#  someone who controls the repo itself. For that, compare against a hash you got
#  through another channel and pass it with --sha256.
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail
umask 022

RED=$'\e[1;31m'; GREEN=$'\e[2;32m'; YELLOW=$'\e[1;33m'; NC=$'\e[0m'
ok()   { printf '%s[+] %s%s\n' "${GREEN}" "$*" "${NC}"; }
info() { printf '%s[*] %s%s\n' "${GREEN}" "$*" "${NC}"; }
warn() { printf '%s[!] %s%s\n' "${YELLOW}" "$*" "${NC}" >&2; }
die()  { printf '%s[-] %s%s\n' "${RED}" "$*" "${NC}" >&2; exit 1; }

REPO="CIAUB/PG-Backup"
RAW_BASE="https://raw.githubusercontent.com/${REPO}"
DEFAULT_TAG="v5.0.0"                 # must equal PGB_VERSION in pg_backup.py (checked by tests)
INSTALL_PATH="/usr/local/bin/PG-Backup"

TAG=""; OFFLINE=0; USE_MAIN=0; EXPECT_SHA=""

# `bash -c "$(curl …)" 5.0.0` puts the version in $0 — accept that form too.
if [[ "${0}" =~ ^[vV]?[0-9]+(\.[0-9]+){1,2}$ ]]; then TAG="${0}"; fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --main)    USE_MAIN=1 ;;
    --sha256)  shift; [ "$#" -gt 0 ] || die "--sha256 needs a value"; EXPECT_SHA="${1,,}" ;;
    --sha256=*) EXPECT_SHA="${1#*=}"; EXPECT_SHA="${EXPECT_SHA,,}" ;;
    -h|--help) sed -n '2,26p' "$0" 2>/dev/null || true; exit 0 ;;
    -*)        die "unknown option: $1" ;;
    *)         TAG="$1" ;;
  esac
  shift
done

if [ -n "${EXPECT_SHA}" ] && ! [[ "${EXPECT_SHA}" =~ ^[0-9a-f]{64}$ ]]; then
  die "--sha256 must be 64 hex characters"
fi

# Tag must look like a version: it ends up in a URL, never in a shell command.
if [ -n "${TAG}" ]; then
  [[ "${TAG}" =~ ^[vV]?[0-9]+(\.[0-9]+){1,2}$ ]] || die "invalid version tag: ${TAG} (expected e.g. v5.0.0)"
  TAG="v${TAG#[vV]}"
fi

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo)."

# ── private scratch dir, always removed ──────────────────────────────────────
WORK="$(mktemp -d /tmp/pgbackup-install.XXXXXXXX)"
chmod 700 "${WORK}"
trap 'rm -rf -- "${WORK}"' EXIT
trap 'die "interrupted"' INT TERM

info "Installing PG-Backup…"

# ── prerequisites (only what the installer itself needs) ─────────────────────
if command -v apt-get >/dev/null 2>&1; then
  if ! command -v python3 >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1 || warn "apt-get update failed; continuing"
    apt-get install -y python3 curl ca-certificates >/dev/null 2>&1 || true
  fi
fi
command -v python3 >/dev/null 2>&1 || die "python3 is required."
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required (coreutils)."

SRC="${WORK}/pg_backup.py"

# ── obtain the script ────────────────────────────────────────────────────────
if [ "${OFFLINE}" -eq 1 ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || die "cannot locate install.sh directory"
  [ -f "${HERE}/pg_backup.py" ] || die "--offline given but no pg_backup.py next to install.sh."
  cp -- "${HERE}/pg_backup.py" "${SRC}"
  if [ -f "${HERE}/pg_backup.py.sha256" ]; then
    printf '%s\n' "$(cut -d' ' -f1 "${HERE}/pg_backup.py.sha256")  ${SRC}" >"${WORK}/sum" || true
  fi
  info "Source: local offline copy (${HERE}/pg_backup.py)"
else
  if [ "${USE_MAIN}" -eq 1 ]; then
    REF="main"; warn "Tracking the moving 'main' branch — not an immutable release."
  else
    REF="${TAG:-${DEFAULT_TAG}}"
  fi
  info "Source: ${REF}"
  command -v curl >/dev/null 2>&1 || die "curl is required."
  fetch() {  # fetch <url> <dest>
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 180 \
         -o "$2" "$1" || return 1
  }
  fetch "${RAW_BASE}/${REF}/pg_backup.py" "${SRC}" \
    || die "Download failed: ${RAW_BASE}/${REF}/pg_backup.py (does the tag exist? network OK?)"
  if fetch "${RAW_BASE}/${REF}/pg_backup.py.sha256" "${WORK}/pg_backup.py.sha256"; then
    PUBLISHED="$(awk 'NR==1{print tolower($1)}' "${WORK}/pg_backup.py.sha256")"
    [[ "${PUBLISHED}" =~ ^[0-9a-f]{64}$ ]] || die "pg_backup.py.sha256 is malformed."
    printf '%s  %s\n' "${PUBLISHED}" "${SRC}" >"${WORK}/sum"
  else
    [ -n "${EXPECT_SHA}" ] || die "pg_backup.py.sha256 could not be downloaded and no --sha256 was given; refusing to install unverified code."
    warn "pg_backup.py.sha256 not found; relying on the --sha256 you supplied."
  fi
fi

[ -s "${SRC}" ] || die "Downloaded file is empty."
[ "$(stat -c %s "${SRC}")" -lt 5000000 ] || die "Downloaded file is unexpectedly large."

# ── integrity ────────────────────────────────────────────────────────────────
ACTUAL="$(sha256sum "${SRC}" | awk '{print $1}')"
if [ -n "${EXPECT_SHA}" ]; then
  [ "${ACTUAL}" = "${EXPECT_SHA}" ] || die "SHA-256 mismatch against the value you supplied (got ${ACTUAL})."
  ok "SHA-256 matches the hash you supplied."
fi
if [ -f "${WORK}/sum" ]; then
  sha256sum --status -c "${WORK}/sum" || die "SHA-256 mismatch against pg_backup.py.sha256 (got ${ACTUAL}). Nothing was installed."
  ok "SHA-256 verified against pg_backup.py.sha256."
elif [ "${OFFLINE}" -eq 1 ] && [ -z "${EXPECT_SHA}" ]; then
  warn "Offline install without a hash — you are trusting the local file."
fi
ok "SHA-256: ${ACTUAL}"

# ── sanity: it must be valid Python and really be PG-Backup ──────────────────
python3 -I - "${SRC}" <<'PY' || die "The downloaded file is not a valid PG-Backup script. Nothing was installed."
import ast, sys
src = open(sys.argv[1], encoding="utf-8").read()
ast.parse(src)
if "Pasarguard Backup Utility" not in src[:1000]:
    raise SystemExit("not PG-Backup")
PY

VERSION="$(head -40 "${SRC}" | grep -m1 -oE 'v[0-9]+\.[0-9]+(\.[0-9]+)?' || true)"
ok "Version: ${VERSION:-unknown}"

# ── shebang (keep hash check above on the pristine file) ─────────────────────
if ! head -n 1 "${SRC}" | grep -q '^#!.*python'; then
  { printf '%s\n' '#!/usr/bin/env python3'; cat -- "${SRC}"; } >"${WORK}/with_shebang"
  mv -f -- "${WORK}/with_shebang" "${SRC}"
fi

# ── atomic install ───────────────────────────────────────────────────────────
mkdir -p -- "$(dirname "${INSTALL_PATH}")"
NEW="$(dirname "${INSTALL_PATH}")/.PG-Backup.new.$$"
install -m 0755 -o root -g root -- "${SRC}" "${NEW}"
mv -f -- "${NEW}" "${INSTALL_PATH}"
ok "Installed to ${INSTALL_PATH}"

if [ -t 0 ] && [ -t 1 ]; then
  info "Launching PG-Backup…"
  echo
  rm -rf -- "${WORK}"; trap - EXIT      # exec replaces the shell, so clean up first
  exec "${INSTALL_PATH}"
fi
info "Run it with:  sudo PG-Backup"
