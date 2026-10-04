# Changelog

## 2.1.8
- The project is released under the MIT License, with a list of the third-party software the app uses and
  their licenses (`THIRD_PARTY_NOTICES.md`), a security policy and issue forms.
- Fix: the Leases tab and Delete lost leases about an hour after start. Kea tidies its lease file every hour and moves
  the old state to `.1`/`.2` files; those are now read too (in Kea's own load order).
- Fix: Save & apply now watches Kea for a moment after applying. If Kea stops, or rejects a reload, even though its
  configuration test passed, the previous configuration is restored and the problem is shown. Before, the Web UI reported
  success and the broken setting stayed saved. (A configuration that fails Kea's own test, such as a network interface
  that does not exist, was already refused before anything was changed.)
- Fix: for IPv6 leases the identifier is the DUID (Reserve used the MAC address when Kea had recorded one), and delegated
  prefixes are shown with their length and cannot be reserved as addresses.
- Fix: a damaged `settings.json` no longer crashes the app on start-up or on import; a section that cannot be read is
  treated as having no subnets.
- Fix: a Kea that has only just started is restarted instead of reloaded (a reload signal sent too early would kill it);
  deleting a lease no longer starts a daemon that was stopped; importing a file without a `global` section keeps the
  current interface instead of the app's initial one.
- Security: the Web UI no longer takes the caller's address from `X-Forwarded-For`, so another process on the same host
  cannot pass itself off as the Supervisor; the JSON content type must match exactly, and chunked request bodies are refused.
- Web UI: the messages after saving say what really happened (DHCPv6 started or stopped, Kea restarted or reloaded); the
  form is locked while a save is running; the log view no longer shows lines twice after changing the filter.

## 2.1.7
- Wording: Home Assistant now calls add-ons "apps" (since 2026.2), so the Web UI, the documentation, the log
  messages and the repository name say "app". The documented install steps now use the current menu names
  (Settings > Apps > Install app > Repositories). Settings, data and behaviour are unchanged.

## 2.1.6
- Requires Home Assistant 2026.7.0 or newer (`homeassistant` in `config.yaml`). The add-on stores its data with the
  `app_config` mapping, which older Supervisors silently do not mount, so settings and leases would be lost on every
  restart. Older versions are no longer offered the add-on.

## 2.1.5
- Documentation: a full user guide (quick start, every setting explained, using each tab, IPv6 notes, troubleshooting) in the add-on's Documentation tab and the README.

## 2.1.4
- Removed the Traditional Chinese (`zh-Hant`) translation of the configuration options; the add-on is English only.

## 2.1.3
- Reproducible builds: every Python dependency of the Web UI, transitive ones included, is now pinned in
  `requirements.txt` (previously only two packages were pinned), except `pydantic-core`, which pydantic pins itself.
  No functional change.

## 2.1.2
- Fix: Kea 3.0 refused to start because lease files were outside its default directory
  (`invalid path specified: '/config/kea', supported path is '/var/lib/kea'`). The directory is now
  passed with `KEA_DHCP_DATA_DIR`, the variable Kea 3.x actually reads (`KEA_LEASE_DIR` does not exist).
- `run.sh` uses `with-contenv` so the container environment reaches the Web UI.

## 2.1.1
- Fix: remove `NET_BIND_SERVICE` from `privileged` (not a valid Supervisor capability; it made the app
  fail validation). Binding UDP 67/547 does not need it.
- Store settings and leases in the app's own config directory (`app_config`) instead of Home Assistant's
  `/config`. Warn in the Web UI if that storage is not mounted.
- Dockerfile: explicit `FROM` and the required `io.hass.*` labels. Marked as `stage: experimental`.

## 2.1.0
- Web UI: multiple pools per subnet, domain name, search domains, NTP servers, custom DHCP options,
  DHCPv6 prefix delegation, lease filter/sort/reserve/delete, live logs, settings export/import.
- Applying settings reloads Kea (SIGHUP) instead of restarting it where possible.
- Interface and DHCPv6 can be changed in the Web UI; the Configuration tab values are initial defaults.
- English UI.

## 2.0.0
- Ingress Web UI (FastAPI) for subnets, pools and static reservations.
