#!/usr/bin/env bash
set -euo pipefail

OPTIONS=/data/options.json
LEASE_DIR=/config/kea
CONF_DIR=/etc/kea

log() { echo "[kea-addon] $*"; }

opt() { jq -r --arg k "$1" '.[$k]' "$OPTIONS"; }

# 將 "a, b , c" 轉為 JSON array，再以 ", " 串成 Kea option-data 所需字串
dns_to_kea() {
  jq -rn --arg s "$1" \
    '$s | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0)) | join(", ")'
}

mkdir -p "$LEASE_DIR" "$CONF_DIR" /run/kea
# Kea 2.x 預設只允許特定目錄，透過環境變數放行
export KEA_LEASE_DIR="$LEASE_DIR"
export KEA_PIDFILE_DIR=/run/kea
export KEA_LOCKFILE_DIR=/run/kea

INTERFACE=$(opt interface)
PIDS=()

# ---------------- DHCPv4 ----------------
SUBNET=$(opt subnet)
POOL_START=$(opt pool_start)
POOL_END=$(opt pool_end)
ROUTER=$(opt router)
DNS=$(dns_to_kea "$(opt dns)")
LEASE_TIME=$(opt lease_time)

jq -n \
  --arg iface "$INTERFACE" \
  --arg subnet "$SUBNET" \
  --arg pool "$POOL_START - $POOL_END" \
  --arg router "$ROUTER" \
  --arg dns "$DNS" \
  --argjson lease "$LEASE_TIME" \
  --arg leasefile "$LEASE_DIR/kea-leases4.csv" \
  '{
    Dhcp4: {
      "interfaces-config": { interfaces: [$iface], "dhcp-socket-type": "raw" },
      "lease-database": { type: "memfile", persist: true, name: $leasefile, "lfc-interval": 3600 },
      "valid-lifetime": $lease,
      "option-data": (
        [ { name: "routers", data: $router } ]
        + (if $dns != "" then [ { name: "domain-name-servers", data: $dns } ] else [] end)
      ),
      subnet4: [ { id: 1, subnet: $subnet, pools: [ { pool: $pool } ] } ],
      loggers: [ { name: "kea-dhcp4", output_options: [ { output: "stdout" } ], severity: "INFO" } ]
    }
  }' > "$CONF_DIR/kea-dhcp4.conf"

log "驗證 kea-dhcp4 設定..."
kea-dhcp4 -t "$CONF_DIR/kea-dhcp4.conf"
log "啟動 kea-dhcp4 (interface=$INTERFACE)"
kea-dhcp4 -c "$CONF_DIR/kea-dhcp4.conf" &
PIDS+=($!)

# ---------------- DHCPv6 ----------------
if [ "$(opt enable_dhcp6)" = "true" ]; then
  SUBNET6=$(opt subnet6)
  POOL6_START=$(opt pool6_start)
  POOL6_END=$(opt pool6_end)
  DNS6=$(dns_to_kea "$(opt dns6)")
  LEASE_TIME6=$(opt lease_time6)

  jq -n \
    --arg iface "$INTERFACE" \
    --arg subnet "$SUBNET6" \
    --arg pool "$POOL6_START - $POOL6_END" \
    --arg dns "$DNS6" \
    --argjson lease "$LEASE_TIME6" \
    --arg leasefile "$LEASE_DIR/kea-leases6.csv" \
    '{
      Dhcp6: {
        "interfaces-config": { interfaces: [$iface] },
        "lease-database": { type: "memfile", persist: true, name: $leasefile, "lfc-interval": 3600 },
        "valid-lifetime": $lease,
        "preferred-lifetime": ($lease / 2 | floor),
        "option-data": (if $dns != "" then [ { name: "dns-servers", data: $dns } ] else [] end),
        subnet6: [ { id: 1, subnet: $subnet, pools: [ { pool: $pool } ] } ],
        loggers: [ { name: "kea-dhcp6", output_options: [ { output: "stdout" } ], severity: "INFO" } ]
      }
    }' > "$CONF_DIR/kea-dhcp6.conf"

  log "驗證 kea-dhcp6 設定..."
  kea-dhcp6 -t "$CONF_DIR/kea-dhcp6.conf"
  log "啟動 kea-dhcp6 (interface=$INTERFACE)"
  kea-dhcp6 -c "$CONF_DIR/kea-dhcp6.conf" &
  PIDS+=($!)
else
  log "DHCPv6 已停用 (enable_dhcp6=false)，略過 kea-dhcp6"
fi

# ---------------- 行程守護 ----------------
stop_all() {
  log "收到終止訊號，停止所有 Kea 行程..."
  kill "${PIDS[@]}" 2>/dev/null || true
  wait || true
  exit 0
}
trap stop_all TERM INT

set +e
wait -n
RC=$?
log "有 Kea 行程已退出 (exit code $RC)，關閉容器"
kill "${PIDS[@]}" 2>/dev/null
wait
exit "$RC"
