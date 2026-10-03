#!/usr/bin/env bash
# 啟動 Web UI。server.py 會負責產生 Kea 設定檔、啟動/重啟 kea-dhcp4 / kea-dhcp6，
# 並在收到 SIGTERM 時一併停止它們。
set -euo pipefail

# Kea 3.x 只允許特定目錄，透過環境變數放行租約與 pid/lock 路徑
export KEA_LEASE_DIR=/config/kea
export KEA_PIDFILE_DIR=/run/kea
export KEA_LOCKFILE_DIR=/run/kea

mkdir -p /config/kea /run/kea /etc/kea
cd /app
exec /opt/venv/bin/python3 /app/server.py
