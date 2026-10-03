# ISC Kea DHCP Server

ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) with a Web UI in the Home Assistant sidebar.

## Before you start

- The app uses the host network. **Turn off the DHCP server on your router** (or make sure it hands out a
  different range) before enabling this one.
- A sample `192.168.1.0/24` subnet is created on first start. Open **Kea DHCP** in the sidebar and change it
  to your own network before devices start using it.

## Configuration tab

| Option | Meaning |
|---|---|
| `interface` | Network interface to serve DHCP on (e.g. `eth0`, `enp3s0`). Initial default only. |
| `enable_dhcp6` | Initial value of the DHCPv6 switch. |

After the first save in the Web UI, interface and DHCPv6 are managed in the Web UI's **Settings** tab.

## Web UI

- **IPv4 / IPv6** – subnets, pools, gateway, DNS, lease time, static reservations, domain name,
  search domains, NTP servers, custom options, DHCPv6 prefix delegation.
- **Leases** – active leases; reserve a lease as a static address, or delete it.
- **Logs** – live Kea and app log.
- **Settings** – interface, DHCPv6, export/import.

**Save & apply** validates the input and tests the generated Kea configuration; only if it passes is it
applied (Kea reloads without restarting where possible). Otherwise nothing changes.

## Data

Settings (`settings.json`) and lease files are stored in the app's config directory
(`/addon_configs/<id>_isc_kea_dhcp` on the host) and are included in app backups.
