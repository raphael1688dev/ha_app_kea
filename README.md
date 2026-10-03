# ISC Kea DHCP Add-on for Home Assistant

ISC Kea Dual-Stack DHCP Server（DHCPv4 + 可選 DHCPv6），附 Home Assistant Ingress Web UI。

## 安裝

1. Home Assistant → 設定 → Add-ons → Add-on Store → 右上角選單 → Repositories
2. 加入：`https://github.com/raphael1688dev/ha_app_kea`
3. 安裝「ISC Kea DHCP Server」，設定網卡後啟動。
4. 開啟側邊欄的 **Kea DHCP**，在 Web UI 設定網段。

## 設定分工

| 位置 | 內容 |
|---|---|
| Add-on 設定頁 | `interface`（監聽網卡）、`enable_dhcp6`（是否啟用 DHCPv6） |
| Web UI（Ingress） | 多個 IPv4 / IPv6 網段、Pool、閘道、DNS、租約時間、靜態 IP 綁定、租約列表、服務狀態 |

Web UI 的設定存放於 `/config/kea/settings.json`。按「儲存並套用」時，會先驗證輸入並用 `kea-dhcpX -t` 檢查，
通過才寫入並重啟 Kea；任何一步失敗都不會動到現有設定。

> 預設會建立一個 `192.168.1.0/24` 範例網段，**請先改成你自己的網段**再讓裝置來取得位址。

## 注意事項

- 使用 `host_network`，請確認網路上沒有其他 DHCP Server（例如路由器）同時發放同網段位址。
- 租約記錄與 `settings.json` 保存於 HA 的 `/config/kea/`。
- DHCPv6 預設關閉，需將 `enable_dhcp6` 設為 `true` 並重新啟動 Add-on。
- Web UI 只接受來自 Home Assistant Supervisor（Ingress）的連線，不對外開放。

## 開發

Web UI 後端為 FastAPI（`kea/app/`），前端為單一 `static/index.html`（原生 JS、相對路徑以相容 Ingress）。

```bash
pip install -r requirements-dev.txt
pytest tests
```

測試使用假的 `kea-dhcp4` / `kea-dhcp6` 腳本，不需要安裝 Kea。
