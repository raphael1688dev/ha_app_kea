# ISC Kea DHCP App for Home Assistant

[![CI](https://github.com/raphael1688dev/ha_app_kea/actions/workflows/ci.yml/badge.svg)](https://github.com/raphael1688dev/ha_app_kea/actions/workflows/ci.yml)

ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) for Home Assistant, with a Web UI inside Home Assistant.

> **Status: experimental.** Every push to `main` and every pull request is tested in CI with the real Kea 3.0 on
> amd64: configuration, start-up, reload, refusing a configuration Kea cannot run, lease deletion through Kea's control
> channel and the API. After a merge the aarch64 image is built and its configuration and start-up are tested too.
> The author runs it as the DHCP server of their own Home Assistant (since November 2026), but it has had little testing
> beyond that, so back up your router's DHCP settings before switching over, and please report problems as issues.

## Installation

[![Add this repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fraphael1688dev%2Fha_app_kea)

The button opens the *add repository* dialog of your Home Assistant with this repository filled in. Or do it by hand:

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
   it found, and you can change it there. If the default `eth0` does not exist on your machine, the app fills in the
   interface of the default route.) **Starting the app does not start the DHCP server yet:** on a new installation
   Kea is started by your first **Save & apply** (step 4), so nothing is served before you have set up your network.
2. **Open the Web UI:** click *Open Web UI* on the app's page. Turn on **Show in sidebar** there if you want a
   permanent **Kea DHCP** entry in the sidebar.
3. **IPv4 tab:** a sample subnet `192.168.1.0/24` is already there. Replace its values with your own network:
   *Subnet (CIDR)*, the *Address pools* start and end (the range of addresses to hand out), *Default gateway*
   (your router) and *DNS servers*.
4. Click **Save & apply** in the bar at the bottom. If something is wrong, a list of problems is shown and
   **nothing is changed**. The first successful save starts Kea; from then on it starts together with the app.
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
| Maximum lease time (seconds, optional) | IPv4 only. The longest lease a client may ask for; leave empty to use the lease time. Not smaller than the lease time. |
| Address pools | The ranges handed out automatically, each with a start and an end address inside the subnet. You can have several; they must not overlap. A subnet with no pool only serves its reservations. |
| Reservations | Fixed addresses for specific devices, see below. |

**Reservations.** Identify the device by its **MAC address** (IPv4; written with `:`, `-` or `.` as you like, and a
single digit per part such as `b0:a7:32:6:a5:18` is understood) or its **DUID** (IPv6, hexadecimal), give it a
**Reserved IP** inside the subnet, and optionally a **Hostname** (letters, digits, hyphens and underscores). An
address can be reserved only once, unless you turn on *Allow several devices to share one IP* in the Settings
tab; a MAC/DUID can be reserved only once per subnet. The reserved address may lie inside or outside the pools.

**Advanced options** (fold-out at the bottom of each card):

| Field | Meaning |
|---|---|
| Domain name | IPv4 only. The local domain handed to clients, for example `home.lan`. |
| Search domains (comma separated) | Domains clients append when they look up short names. |
| NTP servers (comma separated) | Time servers for clients: IP addresses, or host names such as `pool.ntp.org`. See *NTP servers* below. |
| Custom DHCP options | Any other option by its Kea name (for example `tftp-server-name` or `boot-file-name`) and its value. Names are lowercase (letters, digits and hyphens), each option may appear once per subnet, and the value is text of up to 255 characters. Kea checks the name and value when you apply. Options that have their own field above cannot be repeated here. |
| Prefix delegation pools | IPv6 only. Prefixes handed to routers behind this one: *Prefix* (no host bits), *Prefix length* and *Delegated length* (not smaller than the prefix length). |

**NTP servers.** In the IPv4 tab you can enter host names, but DHCP can only carry addresses. The app therefore
looks the names up itself when you save and then every hour, asks every address it finds whether it really answers
as a time server (and has a sane clock), and gives clients only the ones that do. Addresses that still work are
kept, so Kea is reloaded only when the list really changes; if a lookup fails, the previous addresses stay. At most 16
addresses are sent to clients. Below
the field you see which addresses are sent now. **Check NTP servers** tests what you typed without saving,
**Refresh addresses now** repeats the lookup, and **Use this machine** fills in this machine's own address
(use it when an NTP server such as the Chrony app runs on the Home Assistant machine; Chrony must allow
clients and publish UDP port 123). In the IPv6 tab, addresses and multicast addresses are sent as they are and host
names are sent as names for the client to look up; many systems ignore DHCPv6 time servers.

### Settings tab

- **Network interface** and **Enable DHCPv6 server**. Changing the interface restarts Kea.
- **Answer clients that ask for an unknown address:** when a device asks for the address it had before and Kea
  does not know it, Kea says no at once so the device asks for a new one, instead of waiting. Turn it on when this is
  the only DHCP server on the network (the old DHCP server app always did this).
- **Allow several devices to share one IP:** for a device with a wired and a wireless connection that have two MAC
  addresses and one reserved address. Only one of them should be connected at a time.
- **Import from the DHCP server app:** see *Moving from the DHCP server app* below.
- **API access**, **Pending changes** and **Settings versions**: see *API for scripts and AI agents* below.
- **Export settings** downloads your saved settings as one file. **Import settings** loads such a file into
  the editor so you can look it over; nothing changes until you click **Save & apply**.

## Using the Web UI

- **Status bar** (top): the interface, whether DHCPv4 and DHCPv6 are running, and a **Restart Kea** button.
- **Save & apply** (appears when you have unsaved changes): checks your input, has Kea test the new configuration
  (for the services that are switched on), applies it, and then watches Kea for a moment. If Kea cannot run the new
  configuration, the previous one is restored and the problem is shown. Kea reloads without dropping service;
  changing the interface restarts it, and switching DHCPv6 on or off only starts or stops the DHCPv6 service.
  **Discard changes** goes back to the saved settings. If the settings were changed somewhere else after the page loaded
  them (another browser window, or the API), saving is refused instead of undoing that change: click **Discard changes**
  and make your change again.
- **Leases tab:** the addresses currently in use, with a filter box and sortable columns. **Reserve** copies a device
  into the editor as a reservation (review it, then Save & apply); delegated IPv6 prefixes are shown with their
  length and cannot be reserved. To keep the current address of many devices at once, tick them (**Select all**,
  **Select none**) and click **Reserve selected**: devices that are already reserved, or whose subnet is not in the
  editor, are skipped and listed. Devices with a private (random) MAC address are not ticked by default, because the
  device can change that address and the reservation would then stop matching. **Delete** removes a lease: Kea does it itself
  without stopping (if its control channel is not available, the app falls back to stopping Kea for a moment), and the
  device just asks again when its lease runs out.
- **Logs tab:** the live Kea and app log, filterable by daemon or text. The same output is in the app's
  normal log page.

## Moving from the DHCP server app

If you used Home Assistant's "DHCP server" app (the one based on ISC dhcpd):

1. Open that app's **Configuration** tab, switch to YAML and copy all of it.
2. In this app's **Settings** tab, paste it under **Import from the DHCP server app** and click
   **Convert and load**. Networks, devices, DNS, NTP servers, domain and lease times are loaded into the editor.
   A report lists what was changed or could not be carried over (for example a MAC address that is invalid, or
   used for two different addresses). An entry that is listed twice with the same MAC address and address is kept once.
3. Look at the IPv4 tab, use **Check NTP servers** if you entered time servers, then click **Save & apply**.
4. Stop the old app **before** you start this one, and give the Home Assistant machine a fixed address first.
   Existing leases are not carried over: devices get their address again when they renew, which takes at most one
   lease time (turn **Answer clients that ask for an unknown address** on, as the conversion does, so that happens
   quickly). To go back, stop this app and start the old one.

## API for scripts and AI agents

The app can serve a separate HTTP API for scripts, automations and AI agents. It is **off by default**. Turn it on in the
**Settings** tab under **API access**: tick *Enable the API*, choose a port and the networks that may connect (by default only
private address ranges), click **Save API settings**, then **Create token**. The token is shown once: copy it.

- A token can only look (settings, leases, logs, pool usage) unless you give it more: **propose** (check a change and propose
  it), **apply** (change the settings without asking you), **lease-delete** and **restart**.
- A proposed change is never applied by itself. It waits under **Pending changes**, where you see exactly what it would change
  and press **Approve** or **Reject**. If the settings changed in the meantime, it can no longer be approved. At most 20
  proposals can wait at a time.
- Every applied change is saved as a version (the last 50 are kept). **Settings versions** lists them and **Roll back** goes
  back to one.
- Requests are limited per token, and every call is recorded (who, what, when; never the token).
- The traffic is plain HTTP, so keep it inside your own network, or use a VPN or a TLS proxy. Text that devices on your network
  write (host names, log lines) can be anything: whatever reads it, an AI model in particular, must treat it as data.

The endpoints are described in `docs/agent-api.md` in the repository and by the API itself at `/v1/openapi.json`.

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
| Yellow banner **Kea is not running yet** | Normal on a new installation (or when the saved settings could not be read): check the interface and the subnet, then click **Save & apply**. That starts Kea. |
| Notice that an NTP server "is not sent to clients" | None of the addresses of that host name answered as a time server (or the name could not be looked up). Use **Check NTP servers** to see why; the app tries again every five minutes. |

## Your data

Settings (`settings.json`), the saved versions of them (`history/`), the addresses found for NTP host names (`ntp-servers.json`), the API's tokens (stored only as hashes), proposals and call log, and the lease files are stored in the app's own config folder
(`/addon_configs/<id>_isc_kea_dhcp` on the host) and are included in Home Assistant backups of the app.
<!-- guide:end -->

## License

The code in this repository is released under the [MIT License](LICENSE). The app installs and runs other software
(ISC Kea, Alpine Linux, Python packages), each under its own license, and its icon is the Kea logo, which belongs to ISC:
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This project is not affiliated with ISC or Home Assistant.
Security problems: see [SECURITY.md](SECURITY.md).

## For developers

Architecture notes, running the tests, CI, dependency updates and how to cut a release are in
[docs/development.md](docs/development.md).
