# ISC Kea DHCP Add-on for Home Assistant

ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) with a Home Assistant Ingress Web UI.

## Installation

1. In Home Assistant, go to Settings → Add-ons → Add-on Store → ⋮ menu (top right) → Repositories.
2. Add `https://github.com/raphael1688dev/ha_app_kea`.
3. Install **ISC Kea DHCP Server**, set the network interface, and start it.
4. Open **Kea DHCP** in the sidebar and configure your subnets in the Web UI.

## Features (Web UI)

| Tab | What you can do |
|---|---|
| **IPv4 / IPv6** | Multiple subnets; multiple address pools per subnet; gateway, DNS, lease time; static reservations (MAC for IPv4, DUID for IPv6). Advanced options: domain name, search domains, NTP servers, custom DHCP options by name, DHCPv6 prefix delegation pools |
| **Leases** | Active IPv4 / IPv6 leases with filtering and sorting; **Reserve** turns a lease into a static reservation; **Delete** removes a lease |
| **Logs** | Live Kea and add-on log, filter by daemon or text |
| **Settings** | Network interface, DHCPv6 on/off, settings export and import |

The server status and a **Restart Kea** button are always visible at the top.

## How settings are applied

Web UI settings are stored in `/config/kea/settings.json`. When you click **Save & apply**:

1. The input is validated.
2. The generated Kea configuration is checked with `kea-dhcpX -t`.
3. Only if both pass is the configuration written. Running daemons are told to reload it (`SIGHUP`), so
   leases and service are not interrupted. Changing the network interface, enabling DHCPv6, or a daemon
   that is not running causes a (re)start instead.

If any step fails, the running configuration is left untouched and the errors are shown.

**Import** loads a settings file into the editor (older single-pool files are upgraded automatically) and
changes nothing until you click **Save & apply**.

## Add-on Configuration tab

`interface` and `enable_dhcp6` in the add-on Configuration tab are only the **initial defaults** used until
the first save in the Web UI. After that, the Web UI's Settings tab is authoritative.

> A sample `192.168.1.0/24` subnet is created on first start. **Change it to your own network** before
> letting devices obtain addresses from this server.

## Notes

- The add-on uses `host_network`. Make sure no other DHCP server (for example your router) is handing out
  addresses on the same network.
- Lease files and `settings.json` are kept in `/config/kea/` in Home Assistant.
- **Deleting a lease** briefly stops the Kea daemon while the lease file is edited. The client keeps its
  address until the lease runs out and then asks again.
- Kea output is also written to the add-on log in Home Assistant.
- The Web UI only accepts connections from the Home Assistant Supervisor (Ingress). It is not exposed
  anywhere else.

## Development

The Web UI backend is FastAPI (`kea/app/`). The frontend is a single `static/index.html` using plain
JavaScript and relative URLs so it works behind Ingress.

```bash
pip install -r requirements-dev.txt
pytest tests
```

Tests use fake `kea-dhcp4` / `kea-dhcp6` scripts, so Kea does not need to be installed.
