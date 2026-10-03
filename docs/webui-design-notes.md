# Web UI（HA Ingress）設計記錄

> 記錄日期：2026-10-03　狀態：**實作完成，待真實 HA 驗證**

## 1. 需求
- 需要 Web UI 進行設定，且**只透過 Home Assistant Ingress 開啟**（不對外開放額外連接埠給使用者）。

## 2. 已達成共識的分工

| 層級 | 放哪裡 | 內容 |
|---|---|---|
| 部署層級 | HA App 設定頁（`options`/`schema`） | `interface`、`enable_dhcp6`（必要時加日誌等級） |
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

## 5. 進度

| 項目 | 狀態 |
|---|---|
| `kea/app/settings.py`（驗證與設定檔渲染） | ✅ 完成，已有單元測試 |
| `kea/app/leases.py`（租約 CSV 讀取） | ✅ 完成，已有單元測試 |
| `kea/app/manager.py`（行程管理） | ✅ 完成，以假 Kea 腳本測試 |
| `kea/app/server.py`（FastAPI） | ✅ 完成，TestClient 測試 + 本機實跑 |
| `kea/app/static/index.html`（前端） | ✅ 完成，瀏覽器實測（編輯、錯誤顯示、儲存、租約、XSS） |
| `config.yaml` / `translations` / `Dockerfile` / `run.sh` | ✅ 已改為 Ingress 版（v2.0.0） |
| README | ✅ 已更新 |
| 映像 build、真實 Kea 3.0.x、真實 HA Ingress | ⬜ 未驗證（本機無 Docker / Kea） |

## 6. 未驗證的風險
- 尚未 build 映像：Alpine 3.24 上 pip 安裝 `fastapi`/`pydantic-core` 是否有可用的 musllinux wheel、`kea-dhcp4`/`kea-dhcp6` 套件名稱與版本，皆待確認。
- 尚未在真實 Kea 3.0.x 上跑：Kea 3.0 路徑限制（`KEA_LEASE_DIR` 等環境變數）、`output-options`、`kea -t` 對渲染出的設定是否通過，只有假腳本測過流程。
- Ingress 在 `host_network` 下的來源 IP 是否為 `172.30.32.2`（可用環境變數 `KEA_UI_ALLOWED` 調整；被擋時畫面會顯示 `forbidden (<ip>)`）。
- 預設範例網段 `192.168.1.0/24` 會在首次啟動就生效。
