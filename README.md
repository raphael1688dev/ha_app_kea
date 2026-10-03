# ISC Kea DHCP Add-on for Home Assistant

ISC Kea Dual-Stack DHCP Server（DHCPv4 + 可選 DHCPv6）。

## 安裝

1. Home Assistant → 設定 → Add-ons → Add-on Store → 右上角選單 → Repositories
2. 加入：`https://github.com/raphael1688dev/ha_app_kea`
3. 安裝「ISC Kea DHCP Server」，調整設定後啟動。

## 注意事項

- 使用 `host_network`，請確認網路上沒有其他 DHCP Server（例如路由器）同時發放同網段位址。
- 租約記錄保存於 HA 的 `/config/kea/`。
- DHCPv6 預設關閉，需將 `enable_dhcp6` 設為 `true`。
