以下是已將所有欄位預設值（`options`）全部填滿常見標準示範值的完整規格文件，可直接複製提供給 Antigravity：

```markdown
# 專案需求：建立 ISC Kea Dual-Stack DHCP Server 的 Home Assistant 本地 Add-on

請協助在 Home Assistant 中建立一個名為 `isc_kea_dhcp` 的 Local Add-on，支援 DHCPv4 與選擇性啟用的 DHCPv6。

## 1. 核心需求與架構規範
- **網路權限**：DHCP 必須接收廣播與底層發包，因此必須設定 `host_network: true`，且需要 `NET_ADMIN`、`NET_RAW`、`NET_BIND_SERVICE` 特權。
- **介面設定完全暴露**：所有網路參數均需在 Home Assistant 的「設定（Configuration）」頁面中暴露，並提供繁體中文說明。
- **預設值規範**：`options` 欄位必須全部填滿預設數值，初次安裝時使用者可直接看到完整範例並視需求修改。
- **持久化儲存**：租約記錄請保存於 `/config/kea/` 目錄下（掛載 `config:rw`）。
- **DHCPv6 彈性開關**：由 `enable_dhcp6` 布林值控制（預設 `false`）。若使用者啟用，才產生 IPv6 設定並啟動 `kea-dhcp6`。

---

## 2. 目錄與檔案結構
請在 `/addons/local/kea/` 目錄下建立以下檔案：
- `config.yaml`：Add-on 元數據、權限、options 預設值與 schema 定義。
- `translations/zh-Hant.yaml`：設定欄位的繁體中文顯示與提示說明。
- `Dockerfile`：基於 Alpine Linux 安裝 `kea-dhcp4`、`kea-dhcp6`、`jq` 與 `bash`。
- `run.sh`：容器啟動腳本，負責解析 `/data/options.json`、動態渲染設定檔、語法檢查與行程守護。

---

## 3. 設定欄位細節 (config.yaml)

### (1) options 初始預設值（全部填滿）
```yaml
interface: "eth0"
# IPv4 基礎設定
subnet: "192.168.1.0/24"
pool_start: "192.168.1.100"
pool_end: "192.168.1.200"
router: "192.168.1.1"
dns: "1.1.1.1, 8.8.8.8"
lease_time: 86400

# IPv6 基礎設定
enable_dhcp6: false
subnet6: "fd00::/64"
pool6_start: "fd00::1000"
pool6_end: "fd00::2000"
dns6: "2606:4700:4700::1111, 2001:4860:4860::8888"
lease_time6: 86400

```

### (2) schema 型態定義

```yaml
interface: str
subnet: str
pool_start: str
pool_end: str
router: str
dns: str
lease_time: int
enable_dhcp6: bool
subnet6: str
pool6_start: str
pool6_end: str
dns6: str
lease_time6: int

```

---

## 4. translations/zh-Hant.yaml

```yaml
configuration:
  interface:
    name: "實體網路介面"
    description: "欲監聽 DHCP 廣播的網卡名稱（例如：eth0, enp3s0）"
  subnet:
    name: "IPv4 網段 (CIDR)"
    description: "例如：192.168.1.0/24"
  pool_start:
    name: "IPv4 動態發放起始 IP"
    description: "例如：192.168.1.100"
  pool_end:
    name: "IPv4 動態發放結束 IP"
    description: "例如：192.168.1.200"
  router:
    name: "IPv4 預設閘道 (Gateway)"
    description: "路由器的 IP 位址，例如：192.168.1.1"
  dns:
    name: "IPv4 DNS 伺服器"
    description: "以逗號分隔，例如：1.1.1.1, 8.8.8.8"
  lease_time:
    name: "IPv4 租約有效秒數"
    description: "預設 86400（即 24 小時）"
  enable_dhcp6:
    name: "啟用 DHCPv6 服務"
    description: "開啟後將同時分發 IPv6 位址"
  subnet6:
    name: "IPv6 網段 (CIDR)"
    description: "例如：fd00::/64 或 2001:db8:1::/64"
  pool6_start:
    name: "IPv6 動態發放起始 IP"
    description: "例如：fd00::1000"
  pool6_end:
    name: "IPv6 動態發放結束 IP"
    description: "例如：fd00::2000"
  dns6:
    name: "IPv6 DNS 伺服器"
    description: "以逗號分隔，例如：2606:4700:4700::1111, 2001:4860:4860::8888"
  lease_time6:
    name: "IPv6 租約有效秒數"
    description: "預設 86400（即 24 小時）"

```

---

## 5. run.sh 實作邏輯要求

1. **讀取設定**：從 `/data/options.json` 讀取上述所有欄位。
2. **動態渲染 `kea-dhcp4.conf**`：
* 監聽介面設定為 `$interface`。
* 租約資料庫使用 `memfile`，持久化存放於 `/config/kea/kea-leases4.csv`。
* 解析逗號分隔的 DNS 字串轉換為 JSON Array 格式。
* 使用 `kea-dhcp4 -t` 驗證設定語法，通過後在背景執行 `kea-dhcp4 -c /etc/kea/kea-dhcp4.conf &`。


3. **動態渲染 `kea-dhcp6.conf` (若 `enable_dhcp6` 為 `true`)**：
* 監聽介面設定為 `$interface`。
* 租約資料庫存放於 `/config/kea/kea-leases6.csv`。
* 解析 DNS6 字串轉換為 JSON Array。
* 使用 `kea-dhcp6 -t` 驗證語法，通過後在背景執行 `kea-dhcp6 -c /etc/kea/kea-dhcp6.conf &`。
* 若 `enable_dhcp6` 為 `false`，則印出日誌提示已停用 DHCPv6 並略過。


4. **行程守護**：使用 `wait -n` 保持容器前景常駐，並確保當任何核心行程異常退出時容器能正確回應。

請依照以上完整填滿的規格直接建立所有對應檔案。

```

```