# Web UI（HA Ingress）設計記錄

> 記錄日期：2026-10-03　狀態：**實作完成，待真實 HA 驗證**

## 1. 需求
- 需要 Web UI 進行設定，且**只透過 Home Assistant Ingress 開啟**（不對外開放額外連接埠給使用者）。

## 2. 已達成共識的分工

| 層級 | 放哪裡 | 內容 |
|---|---|---|
| 部署層級 | HA App 設定頁（`options`/`schema`） | `interface`、`enable_dhcp6`——**僅作初始預設值**（v2.1.0 起改由 Web UI 的 Settings 分頁管理，存於 `settings.json` 的 `global`） |
| 營運層級 | Web UI（Ingress） | 多個 IPv4/IPv6 網段、Pool、Gateway、DNS、租約時間、靜態 IP 綁定、租約列表、服務狀態 |

- Web UI 的設定存放於 `/config/kea/settings.json`，重啟後保留。
- 套用流程：驗證 → 產生設定檔 → `kea-dhcpX -t` 檢查 → 通過才寫入並重啟 Kea 行程；失敗則不動現有設定。
- 這會取代原 `spec.md` 中「所有欄位都放在 HA 設定頁」的設計（`spec.md` 保留作為歷史需求，未修改）。

## 3. 是否採用 Kea 原生 Web UI（ISC Stork）？
**結論（已確認）：不採用，自製輕量 UI。**

- Kea 本身沒有內建 Web UI，官方 UI 是 **Stork**。
- Stork 需要 `stork-server` + `stork-agent` + PostgreSQL；Alpine 3.24 套件庫查無 Stork 套件。
- Stork 在子路徑（Ingress）下需要反向代理改寫 URL；有自己的登入系統。
- 編輯子網／靜態綁定需要 Kea 載入 `host_cmds`、`subnet_cmds` Hook。
- 備案：日後若需要 Stork 的監控功能，可另外跑官方 Stork 容器，不綁進此 Add-on。
- 註：Stork 的資訊來自文件摘要，未實際部署驗證。

## 4. 技術決策
- 後端：**FastAPI + uvicorn**（使用者指定，取代原先的 `http.server` 方案）。裝在映像內獨立 venv `/opt/venv`，版本固定。路由用同步 `def`（跑在執行緒池，因 manager 會阻塞）；關閉 `/docs`、`/openapi.json`。
- 前端：單一 `index.html`，原生 JS，全部使用**相對路徑**以適配 Ingress。
- 租約：直接讀取 memfile CSV，不引入 `kea-ctrl-agent`（Kea 3.0 已棄用 Control Agent）。
- 安全：僅接受來自 Supervisor（`172.30.32.2`）的連線，其餘回 403；寫入 API 要求 `Content-Type: application/json`。
- Kea 3.0 設定使用 `output-options`（`output_options` 已棄用）。
- `host_network: true` 下 Ingress 的來源 IP 假設為 `172.30.32.2`，**尚未在真實 HA 驗證**。

## 5. 進度（v2.1.0）

| 項目 | 狀態 |
|---|---|
| 多網段、多 Pool、靜態綁定、租約列表、服務狀態 | ✅ 完成，單元測試 + 瀏覽器實測 |
| 進階選項：domain name、search domains、NTP、自訂 option | ✅ 完成（自訂 option 的語意由 `kea -t` 把關） |
| DHCPv6 prefix delegation pools | ✅ 完成 |
| 租約：搜尋、排序、一鍵轉靜態綁定、刪除租約 | ✅ 完成 |
| Web UI 內檢視日誌（擷取 daemon stdout，環形緩衝 3000 行，同時仍轉印到 HA 日誌） | ✅ 完成 |
| 設定匯出 / 匯入（匯入只載入編輯器、不直接套用；v1 格式自動遷移） | ✅ 完成 |
| 套用設定改為 SIGHUP 熱重載（網卡變更、daemon 未執行、啟停 DHCPv6 才重啟） | ✅ 完成，假 daemon 驗證 |
| Web UI 內修改網卡與 DHCPv6 開關 | ✅ 完成（HA options 降為初始預設值） |
| 映像 build、真實 Kea 3.0.x、真實 HA Ingress | ⬜ 未驗證（本機無 Docker / Kea） |

### 設定檔 schema
- v1：每網段單一 `pool_start`/`pool_end`，無 `global`。
- v2：`pools` 清單、`domain_name`/`domain_search`/`ntp_servers`/`custom_options`、v6 的 `pd_pools`、`global`（`interface`、`enable_dhcp6`）。讀取時自動由 v1 遷移。

## 6. 未驗證的風險
- 尚未 build 映像：Alpine 3.24 上 pip 安裝 `fastapi`/`pydantic-core` 是否有可用的 musllinux wheel、`kea-dhcp4`/`kea-dhcp6` 套件名稱與版本，皆待確認。
- ~~路徑限制~~ 已修正（CI 冒煙測試抓到）：Kea 3.0 的租約目錄環境變數是 `KEA_DHCP_DATA_DIR`，不是 `KEA_LEASE_DIR`；`kea -t` 不會開啟租約資料庫，所以只有真的啟動才會暴露這個問題。渲染出的設定（含多 Pool、`pd-pools`、`ntp-servers`、`domain-search`、自訂 option）已由 CI 以真 Kea 3.0.3 的 `-t` 驗證通過。
- **SIGHUP 熱重載**：假 daemon 驗證了「訊號有送到、PID 不變」，但真實 Kea 收到 SIGHUP 後是否如預期重新讀取設定、失敗時是否保留舊設定，尚待實測。
- **刪除租約**：依 Kea memfile 慣例（同位址以最後一列為準、`valid_lifetime=0` 代表已刪除）在 daemon 停止時追加一列；此語意依文件與記憶撰寫，未用真實 Kea 驗證。若 Kea 在 LFC 進行中被停止，檔案狀態亦未測。
- Ingress 在 `host_network` 下的來源 IP 是否為 `172.30.32.2`（可用環境變數 `KEA_UI_ALLOWED` 調整；被擋時畫面會顯示 `forbidden (<ip>)`）。
- 預設範例網段 `192.168.1.0/24` 會在首次啟動就生效。
