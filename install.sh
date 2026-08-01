#!/bin/bash

set -e

RED='\e[1;31m'
GREEN='\e[1;32m'
NC='\e[0m'

REPO_RAW="https://raw.githubusercontent.com/EOAMIR/PG-Backup/main"
INSTALL_PATH="/usr/local/bin/PG-Backup"
TMP_PATH="/tmp/pg_backup.py"

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[-] Please run as root (sudo).${NC}"
    exit 1
fi

echo -e "${GREEN}[*] Installing PG-Backup...${NC}"

if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y python3 python3-pip curl unzip >/dev/null 2>&1 || true
fi

pip3 install --break-system-packages requests urllib3 paramiko >/dev/null 2>&1 || \
pip3 install requests urllib3 paramiko >/dev/null 2>&1 || true

curl -fsSL "${REPO_RAW}/pg_backup.py?v=$(date +%s)" -o "${TMP_PATH}"

if [ ! -s "${TMP_PATH}" ]; then
    echo -e "${RED}[-] Download failed. Check GitHub repo/file name.${NC}"
    exit 1
fi

if ! head -n 1 "${TMP_PATH}" | grep -q "python"; then
    printf '%s\n%s\n' '#!/usr/bin/env python3' "$(cat "${TMP_PATH}")" > "${TMP_PATH}.tmp"
    mv "${TMP_PATH}.tmp" "${TMP_PATH}"
fi

mv "${TMP_PATH}" "${INSTALL_PATH}"
chmod +x "${INSTALL_PATH}"

echo -e "${GREEN}[+] Installed to ${INSTALL_PATH}${NC}"
echo -e "${GREEN}[+] Launching PG-Backup...${NC}"
echo

exec "${INSTALL_PATH}"
