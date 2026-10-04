# ISC Kea DHCP App for Home Assistant

[![CI](https://github.com/raphael1688dev/ha_app_kea/actions/workflows/ci.yml/badge.svg)](https://github.com/raphael1688dev/ha_app_kea/actions/workflows/ci.yml)

ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) for Home Assistant, with a Web UI inside Home Assistant.

> **Status: experimental.** Every push to `main` and every pull request is tested in CI with the real Kea 3.0 on
> amd64: configuration, start-up, reload, refusing a configuration Kea cannot run, and lease-file handling. After a
> merge the aarch64 image is built and its configuration and start-up are tested too. The app has **not been
> installed in a real Home Assistant yet**, so back up your router's DHCP settings before switching over, and please
> report problems as issues.

## Installation

1. In Home Assistant, go to **Settings → Apps** and select **Install app**.
2. In the top-right corner open the ⋮ menu, select **Repositories**, enter
   `https://github.com/raphael1688dev/ha_app_kea` and select **Add**.
3. Find **ISC Kea DHCP Server** in the list, install it, then continue with the quick start below.

<!-- guide:start -->
## What it does

Runs the [ISC Kea](https://www.isc.org/kea/) DHCP server inside Home Assistant. It hands out IPv4 addresses (and,
if you turn it on, IPv6 addresses) to the devices on your network, and you manage it from a Web UI inside Home
Assistant: subnets, address pools, DNS, fixed addresses for chosen devices, and a live view of who has
which address. Use it instead of your router's built-in DHCP server.

## Before you start

- **Only one DHCP server per network.** When you switch this one on, switch off DHCP on your router (or give the
  two servers separate address ranges). Two servers answering the same network cause confusing address clashes.
- **Give the Home Assistant machine a fixed address**, not one it gets from DHCP: it is now the DHCP server itself.
- The app uses the **host network** and needs **Home Assistant 2026.7.0 or newer**. Older versions are not
  offered the app, because they would not keep your settings across restarts.
- Have these at hand: your network in CIDR form (for example `192.168.1.0/24`), your router's address (the
  "gateway", for example `192.168.1.1`) and the name of the network interface of the Home Assistant machine on that
  network (for example `eth0` or `enp3s0`).

## Quick start

1. **Configuration tab:** set `interface` to the network interface on your network and start the app.
   (If you are unsure of the name, start with the default; the **Settings** tab of the Web UI lists the interfaces
   it found, and you can change it there.)
2. **Open the Web UI:** click *Open Web UI* on the app's page. Turn on **Show in sidebar** there if you want a
   permanent **Kea DHCP** entry in the sidebar.
3. **IPv4 tab:** a sample subnet `192.168.1.0/24` is already there. Replace its values with your own network:
   *Subnet (CIDR)*, the *Address pools* start and end (the range of addresses to hand out), *Default gateway*
   (your router) and *DNS servers*.
4. Click **Save & apply** in the bar at the bottom. If something is wrong, a list of problems is shown and
   **nothing is changed**.
5. **Turn off DHCP on your router.**
6. Renew the address on a device (reconnect it, or reboot it). It shows up in the **Leases** tab.

## Settings reference

### The app's Configuration tab

| Option | Meaning |
|---|---|
| `interface` | Network interface to serve DHCP on, for example `eth0`. Initial default only. |
| `enable_dhcp6` | Initial value of the DHCPv6 switch (default off). |

After the first save in the Web UI these two are managed in the Web UI's **Settings** tab, and the values here are
no longer used.

### IPv4 and IPv6 tabs: one card per subnet

| Field | Meaning |
|---|---|
| Subnet (CIDR) | The network, for example `192.168.1.0/24` or `fd00::/64`. Must be the network address (no host bits set). Subnets must not overlap. |
| Default gateway | IPv4 only. Your router; must be inside the subnet. Optional. |
| DNS servers (comma separated) | Addresses of the DNS servers clients should use. IPv4 addresses in the IPv4 tab, IPv6 in the IPv6 tab. |
| Lease time (seconds) | How long a client may keep an address before it must renew: 60 to 31,536,000. Default 86,400 (one day). |
| Address pools | The ranges handed out automatically, each with a start and an end address inside the subnet. You can have several; they must not overlap. A subnet with no pool only serves its reservations. |
| Reservations | Fixed addresses for specific devices, see below. |

**Reservations.** Identify the device by its **MAC address** (IPv4; written with `:`, `-` or `.` as you like) or
its **DUID** (IPv6, hexadecimal), give it a **Reserved IP** inside the subnet, and optionally a **Hostname**. An
address can be reserved only once, and a MAC/DUID only once per subnet. The reserved address may lie inside or
outside the pools.

**Advanced options** (fold-out at the bottom of each card):

| Field | Meaning |
|---|---|
| Domain name | IPv4 only. The local domain handed to clients, for example `home.lan`. |
| Search domains (comma separated) | Domains clients append when they look up short names. |
| NTP servers (comma separated) | IPv4 only. Time servers for clients. |
| Custom DHCP options | Any other option by its Kea name (for example `tftp-server-name` or `boot-file-name`) and its value. Names are lowercase (letters, digits and hyphens), each option may appear once per subnet, and the value is text of up to 255 characters. Kea checks the name and value when you apply. Options that have their own field above cannot be repeated here. |
| Prefix delegation pools | IPv6 only. Prefixes handed to routers behind this one: *Prefix* (no host bits), *Prefix length* and *Delegated length* (not smaller than the prefix length). |

### Settings tab

- **Network interface** and **Enable DHCPv6 server**. Changing the interface restarts Kea.
- **Export settings** downloads your saved settings as one file. **Import settings** loads such a file into
  the editor so you can look it over; nothing changes until you click **Save & apply**.

## Using the Web UI

- **Status bar** (top): the interface, whether DHCPv4 and DHCPv6 are running, and a **Restart Kea** button.
- **Save & apply** (appears when you have unsaved changes): checks your input, has Kea test the new configuration
  (for the services that are switched on), applies it, and then watches Kea for a moment. If Kea cannot run the new
  configuration, the previous one is restored and the problem is shown. Kea reloads without dropping service;
  changing the interface restarts it, and switching DHCPv6 on or off only starts or stops the DHCPv6 service.
  **Discard changes** goes back to the saved settings.
- **Leases tab:** the addresses currently in use, with a filter box and sortable columns. **Reserve** copies a device
  into the editor as a reservation (review it, then Save & apply); delegated IPv6 prefixes are shown with their
  length and cannot be reserved. **Delete** removes a lease; Kea is stopped for a
  moment while that happens, and the device just asks again when its lease runs out.
- **Logs tab:** the live Kea and app log, filterable by daemon or text. The same output is in the app's
  normal log page.

## IPv6 (DHCPv6)

Turn it on in the **Settings** tab, then configure the subnet in the **IPv6** tab. The sample `fd00::/64` is a
private example; use a prefix that is really on your network. The interface needs an IPv6 link-local address.
Clients only ask for DHCPv6 addresses if your router advertises that ("managed" flag in its router advertisements),
and some systems, Android in particular, generally rely on automatic (SLAAC) addresses and do not use DHCPv6 at all.

## Troubleshooting

| What you see | What to check |
|---|---|
| **Request failed** with a list of problems | Fix the listed problems. When you were saving, nothing was changed (a configuration Kea cannot run is restored automatically). |
| Red **Kea problem** banner | Kea exited, or the saved configuration failed its test when Kea was started or restarted. Open the **Logs** tab, fix the setting, then **Save & apply** or **Restart Kea**. |
| Yellow banner starting **Persistent storage is not mounted** | Update Home Assistant (and its Supervisor) to 2026.7.0 or newer, then restart the app. Until then your settings are lost on every restart. |
| `forbidden (...)` instead of the page | The page is only served to Home Assistant's Ingress. Open it with *Open Web UI* or from the sidebar, not by its address. If it happens there too, report the address shown in the message. |
| Devices get no address | Is another DHCP server (your router) still answering? Is `interface` the one on your network? Does the subnet match your network? The Logs tab shows what Kea writes at its default level (start-up, reloads, errors and similar events); individual DHCP packets are not logged. |
| IPv6 tab says DHCPv6 is turned off | Switch it on in the **Settings** tab. |

## Your data

Settings (`settings.json`) and the lease files are stored in the app's own config folder
(`/addon_configs/<id>_isc_kea_dhcp` on the host) and are included in Home Assistant backups of the app.
<!-- guide:end -->

## License

The code in this repository is released under the [MIT License](LICENSE). The app installs and runs other software
(ISC Kea, Alpine Linux, Python packages), each under its own license: see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This project is not affiliated with ISC or Home Assistant.
Security problems: see [SECURITY.md](SECURITY.md).

## For developers

Architecture notes, running the tests, CI, dependency updates and how to cut a release are in
[docs/development.md](docs/development.md).
