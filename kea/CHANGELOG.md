# Changelog

## Unreleased
- Removed the Traditional Chinese (`zh-Hant`) translation of the configuration options; the add-on is English only.

## 2.1.3
- Reproducible builds: every Python dependency of the Web UI, transitive ones included, is now pinned in
  `requirements.txt` (previously only two packages were pinned). No functional change.

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
