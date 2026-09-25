#!/usr/bin/env bash
set -euo pipefail

TARGET_BIN_DIR="${HOME}/.local/bin"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_TARGET="${TARGET_BIN_DIR}/smart-downloads-daemon"

echo "=== Installing smart-downloads-daemon ==="
mkdir -p "${TARGET_BIN_DIR}" "${SYSTEMD_USER_DIR}"

# 1. Install executable runner pointing to src/
cat << EOF > "${BIN_TARGET}"
#!/usr/bin/env python3
import sys
from pathlib import Path

src_dir = Path("${SCRIPT_DIR}/src")
if src_dir.exists() and str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from smart_downloads_daemon.cli import main

if __name__ == "__main__":
    main()
EOF
chmod +x "${BIN_TARGET}"
echo "✓ Installed executable to ${BIN_TARGET}"

# 2. Compatibility symlink for organize_downloads.py
ln -sf "${BIN_TARGET}" "${TARGET_BIN_DIR}/organize_downloads.py"
echo "✓ Created compatibility symlink: ${TARGET_BIN_DIR}/organize_downloads.py -> smart-downloads-daemon"

# 3. Clean up legacy service if active/enabled, and install modern systemd user service unit
LEGACY_SERVICE="downloads-organizer.service"
if systemctl --user is-active "${LEGACY_SERVICE}" &>/dev/null; then
    echo "• Stopping legacy ${LEGACY_SERVICE}..."
    systemctl --user stop "${LEGACY_SERVICE}" 2>/dev/null || true
fi
if systemctl --user is-enabled "${LEGACY_SERVICE}" &>/dev/null; then
    echo "• Disabling legacy ${LEGACY_SERVICE}..."
    systemctl --user disable "${LEGACY_SERVICE}" 2>/dev/null || true
fi
rm -f "${SYSTEMD_USER_DIR}/${LEGACY_SERVICE}"

SERVICE_FILE="${SCRIPT_DIR}/systemd/smart-downloads-daemon.service"
if [[ -f "${SERVICE_FILE}" ]]; then
  cp -f "${SERVICE_FILE}" "${SYSTEMD_USER_DIR}/smart-downloads-daemon.service"
  systemctl --user daemon-reload 2>/dev/null || true
  systemctl --user enable --now smart-downloads-daemon.service 2>/dev/null || true
  echo "✓ Installed and enabled systemd service: smart-downloads-daemon.service"
fi

# 4. Verification
echo ""
echo "Verifying installation:"
"${BIN_TARGET}" --version
if systemctl --user is-active --quiet smart-downloads-daemon; then
  echo "✓ Background daemon is active and running!"
else
  echo "! Warning: Background daemon is not active. Check 'journalctl --user -u smart-downloads-daemon -n 20'"
fi

echo ""
echo "=== Systemd Service Management ==="
echo "To check daemon status:"
echo "  systemctl --user status smart-downloads-daemon"
echo ""
echo "To view live logs:"
echo "  journalctl --user -u smart-downloads-daemon -f"
echo ""
echo "Installation complete!"
