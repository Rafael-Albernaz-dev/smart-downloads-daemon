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

# 3. Install systemd user service unit
SERVICE_FILE="${SCRIPT_DIR}/systemd/smart-downloads-daemon.service"
if [[ -f "${SERVICE_FILE}" ]]; then
  cp -f "${SERVICE_FILE}" "${SYSTEMD_USER_DIR}/smart-downloads-daemon.service"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "✓ Installed systemd service to ${SYSTEMD_USER_DIR}/smart-downloads-daemon.service"
fi

# 4. Verification
echo ""
echo "Verifying installation:"
"${BIN_TARGET}" --version

echo ""
echo "=== Systemd Service Management ==="
echo "To enable and start the background daemon on boot:"
echo "  systemctl --user enable --now smart-downloads-daemon"
echo ""
echo "To check daemon status:"
echo "  systemctl --user status smart-downloads-daemon"
echo ""
echo "To view live logs:"
echo "  journalctl --user -u smart-downloads-daemon -f"
echo ""
echo "Installation complete!"
