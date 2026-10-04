> **Historical document.** This is the original brief the add-on was first built from. It has since been
> superseded: network settings now live in the Web UI rather than the add-on Configuration tab
> (see [docs/webui-design-notes.md](docs/webui-design-notes.md)), and two requirements below turned out to be wrong
> for current Home Assistant: `NET_BIND_SERVICE` is not a valid `privileged` capability, and the `config` map type
> is deprecated in favour of `app_config`. The text is kept as written, translated to English.

Below is the complete specification with every default value in `options` filled in with common standard example
values. It can be handed to Antigravity as is:

````markdown
# Project requirements: a local Home Assistant add-on for the ISC Kea dual-stack DHCP server

Please create a local Home Assistant add-on named `isc_kea_dhcp` that supports DHCPv4 and an optional DHCPv6.

## 1. Core requirements and architecture rules
- **Network permissions**: DHCP has to receive broadcasts and send raw packets, so `host_network: true` is required,
  together with the `NET_ADMIN`, `NET_RAW` and `NET_BIND_SERVICE` privileges.
- **Fully exposed interface settings**: every network parameter must be exposed on the Home Assistant
  "Configuration" page, with descriptions.
- **Default value rule**: every field in `options` must be filled with a default value, so that after the first
  installation the user sees a complete example and can adjust it as needed.
- **Persistent storage**: lease records are kept under `/config/kea/` (mapped with `config:rw`).
- **DHCPv6 switch**: controlled by the boolean `enable_dhcp6` (default `false`). Only when the user enables it are the
  IPv6 configuration generated and `kea-dhcp6` started.

---

## 2. Directory and file layout
Create the following files in `/addons/local/kea/`:
- `config.yaml`: add-on metadata, permissions, `options` defaults and `schema` definition.
- `translations/zh-Hant.yaml`: display names and hints for the configuration fields.
- `Dockerfile`: based on Alpine Linux, installs `kea-dhcp4`, `kea-dhcp6`, `jq` and `bash`.
- `run.sh`: container start-up script that parses `/data/options.json`, renders the configuration files dynamically,
  checks their syntax and supervises the processes.

---

## 3. Configuration field details (config.yaml)

### (1) Initial `options` defaults (all filled in)
```yaml
interface: "eth0"
# IPv4 basics
subnet: "192.168.1.0/24"
pool_start: "192.168.1.100"
pool_end: "192.168.1.200"
router: "192.168.1.1"
dns: "1.1.1.1, 8.8.8.8"
lease_time: 86400

# IPv6 basics
enable_dhcp6: false
subnet6: "fd00::/64"
pool6_start: "fd00::1000"
pool6_end: "fd00::2000"
dns6: "2606:4700:4700::1111, 2001:4860:4860::8888"
lease_time6: 86400
```

### (2) `schema` types
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
    name: "Physical network interface"
    description: "Name of the network interface that listens for DHCP broadcasts (for example: eth0, enp3s0)"
  subnet:
    name: "IPv4 subnet (CIDR)"
    description: "For example: 192.168.1.0/24"
  pool_start:
    name: "IPv4 dynamic pool start IP"
    description: "For example: 192.168.1.100"
  pool_end:
    name: "IPv4 dynamic pool end IP"
    description: "For example: 192.168.1.200"
  router:
    name: "IPv4 default gateway"
    description: "IP address of the router, for example: 192.168.1.1"
  dns:
    name: "IPv4 DNS servers"
    description: "Comma separated, for example: 1.1.1.1, 8.8.8.8"
  lease_time:
    name: "IPv4 lease time in seconds"
    description: "Default 86400 (24 hours)"
  enable_dhcp6:
    name: "Enable the DHCPv6 service"
    description: "When on, IPv6 addresses are handed out as well"
  subnet6:
    name: "IPv6 subnet (CIDR)"
    description: "For example: fd00::/64 or 2001:db8:1::/64"
  pool6_start:
    name: "IPv6 dynamic pool start IP"
    description: "For example: fd00::1000"
  pool6_end:
    name: "IPv6 dynamic pool end IP"
    description: "For example: fd00::2000"
  dns6:
    name: "IPv6 DNS servers"
    description: "Comma separated, for example: 2606:4700:4700::1111, 2001:4860:4860::8888"
  lease_time6:
    name: "IPv6 lease time in seconds"
    description: "Default 86400 (24 hours)"
```

---

## 5. run.sh implementation requirements

1. **Read the settings**: read all of the fields above from `/data/options.json`.
2. **Render `kea-dhcp4.conf` dynamically**:
   * Set the listening interface to `$interface`.
   * Use the `memfile` lease database, persisted at `/config/kea/kea-leases4.csv`.
   * Convert the comma separated DNS string into a JSON array.
   * Check the syntax with `kea-dhcp4 -t`, then start `kea-dhcp4 -c /etc/kea/kea-dhcp4.conf &` in the background.
3. **Render `kea-dhcp6.conf` (only when `enable_dhcp6` is `true`)**:
   * Set the listening interface to `$interface`.
   * Store the lease database at `/config/kea/kea-leases6.csv`.
   * Convert the DNS6 string into a JSON array.
   * Check the syntax with `kea-dhcp6 -t`, then start `kea-dhcp6 -c /etc/kea/kea-dhcp6.conf &` in the background.
   * When `enable_dhcp6` is `false`, log that DHCPv6 is disabled and skip it.
4. **Process supervision**: keep the container in the foreground with `wait -n`, and make sure the container reacts
   correctly when any core process exits abnormally.

Please create all the corresponding files according to the fully filled-in specification above.
````
