#!/usr/bin/env python3
"""End-to-end smoke test of the running add-on (stdlib only).

Runs *inside* the container started from the built image, against the real Kea:
startup, hot reload (SIGHUP), lease deletion and restart. Environment:
  SMOKE_BASE      Web UI base URL            (default http://127.0.0.1:8099)
  SMOKE_DATA_DIR  where Kea keeps its leases (default /config/kea)
Exit code 0 = all good; otherwise the last status/logs are printed and 1 is returned.
"""
import copy
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8099")
DATA_DIR = os.environ.get("SMOKE_DATA_DIR", "/config/kea")
# Kea log message ids that mean "the configuration or the lease file was not accepted".
BAD_LOG_MARKERS = ("CONFIG_LOAD_FAIL", "CONFIG_FAILED", "LEASE_LOAD_ROW_ERROR", "LOAD_ROW_ERROR",
                   "LEASE_LOAD_ERROR", "FATAL")
DEFAULT_V4_HEADER = ("address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,"
                     "hostname,state,user_context,pool_id")


class Failure(Exception):
    pass


def call(method, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if method != "GET":
        req.add_header("Content-Type", "application/json")
        req.data = req.data or b"{}"
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            raise Failure("the Web UI answered 403 (forbidden): KEA_UI_ALLOWED did not reach the "
                          "server; check that run.sh uses with-contenv")
        try:
            return err.code, json.loads(err.read().decode())
        except ValueError:
            return err.code, None


def wait_for(predicate, what, timeout=60, interval=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = predicate()
        except Failure:
            raise
        except Exception:
            value = None
        if value:
            return value
        time.sleep(interval)
    raise Failure(f"timed out after {timeout}s waiting for: {what}")


def status():
    return call("GET", "/api/status")[1]


def daemon(name="dhcp4"):
    return status()["daemons"][name]


def log_entries():
    return (call("GET", "/api/logs?limit=3000")[1] or {}).get("entries", [])


def check_log_clean(since_seq, what):
    bad = [e["line"] for e in log_entries() if e["seq"] > since_seq
           and e["source"] != "addon" and any(m in e["line"] for m in BAD_LOG_MARKERS)]
    if bad:
        raise Failure(f"Kea reported a problem after {what}:\n  " + "\n  ".join(bad[:8]))


def last_seq():
    return call("GET", "/api/logs?limit=1")[1]["last_seq"]


def step(message):
    print(f"==> {message}", flush=True)


def apply_settings(settings):
    code, body = call("PUT", "/api/settings", settings)
    if code != 200 or not body.get("ok"):
        raise Failure(f"applying settings failed (HTTP {code}): {body}")
    return body


def run():
    step("waiting for Kea DHCPv4 to start")
    def started():
        st = status()
        if st["daemons"]["dhcp4"]["running"]:
            return True
        if st["daemons"]["dhcp4"]["exit_code"] is not None or st["last_error"]:
            raise Failure("kea-dhcp4 exited right after starting: " + str(st["last_error"]))
        return False

    wait_for(started, "kea-dhcp4 running", timeout=90)
    d = daemon()
    if not d["installed"]:
        raise Failure("kea-dhcp4 is not installed in the image")
    v4_lines = [e["line"] for e in log_entries() if e["source"] == "dhcp4"]
    print(f"    kea-dhcp4 pid {d['pid']}, {len(v4_lines)} log lines; first: {v4_lines[0][:140] if v4_lines else '-'}")
    time.sleep(2)
    if not daemon()["running"]:
        raise Failure("kea-dhcp4 exited shortly after start")
    check_log_clean(0, "startup")

    step("hot reload: apply a feature-rich configuration (expects SIGHUP, same process)")
    settings = call("GET", "/api/state")[1]["settings"]
    rich = copy.deepcopy(settings)
    sub = rich["dhcp4"]["subnets"][0]
    sub["pools"].append({"start": "192.168.1.10", "end": "192.168.1.20"})
    sub.update(domain_name="home.lan", domain_search="home.lan, lan", ntp_servers="192.168.1.2",
               lease_time=7200,
               custom_options=[{"name": "tftp-server-name", "data": "192.168.1.5"}],
               reservations=[{"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50",
                              "hostname": "nas"}])
    pid, mark = daemon()["pid"], last_seq()
    result = apply_settings(rich)
    if result["action"] != "reloaded":
        raise Failure(f"expected action 'reloaded', got {result['action']!r}")
    time.sleep(4)
    d = daemon()
    if not d["running"] or d["pid"] != pid:
        raise Failure(f"kea-dhcp4 did not survive the reload (running={d['running']}, pid {pid} -> {d['pid']})")
    check_log_clean(mark, "the SIGHUP reload")
    reload_lines = [e["line"] for e in log_entries() if e["seq"] > mark and e["source"] == "dhcp4"]
    print(f"    still running as pid {pid}; Kea logged {len(reload_lines)} lines after the signal")

    step("lease deletion: Kea must accept the lifetime-0 row we append")
    lease_file = os.path.join(DATA_DIR, "kea-leases4.csv")
    if os.path.isfile(lease_file):
        with open(lease_file, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
    else:
        header = DEFAULT_V4_HEADER.split(",")
        with open(lease_file, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerow(header)
    row = {name: "" for name in header}
    row.update(address="192.168.1.150", hwaddr="aa:bb:cc:dd:ee:99", valid_lifetime="86400",
               expire=str(int(time.time()) + 86400), subnet_id="1", fqdn_fwd="0", fqdn_rev="0",
               hostname="smoke", state="0")
    with open(lease_file, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=header, lineterminator="\n").writerow(row)
    leases = call("GET", "/api/leases")[1]["dhcp4"]
    if "192.168.1.150" not in [l["address"] for l in leases]:
        raise Failure("the seeded lease is not listed by /api/leases")
    pid, mark = daemon()["pid"], last_seq()
    code, body = call("DELETE", "/api/leases/dhcp4/192.168.1.150")
    if code != 200:
        raise Failure(f"deleting the lease failed (HTTP {code}): {body}")
    wait_for(lambda: daemon()["running"] and daemon()["pid"] != pid, "kea-dhcp4 restarted after the delete")
    time.sleep(4)
    if not daemon()["running"]:
        raise Failure("kea-dhcp4 died after reading the lease file with the deleted-lease row")
    check_log_clean(mark, "the lease deletion")
    if "192.168.1.150" in [l["address"] for l in call("GET", "/api/leases")[1]["dhcp4"]]:
        raise Failure("the lease is still listed after deletion")

    step("restart endpoint")
    pid = daemon()["pid"]
    code, body = call("POST", "/api/restart")
    if code != 200 or not body.get("ok"):
        raise Failure(f"restart failed (HTTP {code}): {body}")
    wait_for(lambda: daemon()["running"] and daemon()["pid"] != pid, "kea-dhcp4 restarted")

    step("DHCPv6 (best effort: IPv6 support of CI runners and containers varies)")
    v6 = copy.deepcopy(rich)
    v6["global"]["enable_dhcp6"] = True
    v6["dhcp6"]["subnets"][0].update(
        domain_search="home.lan",
        pd_pools=[{"prefix": "2001:db8:1::", "prefix_len": 48, "delegated_len": 64}])
    apply_settings(v6)
    try:
        wait_for(lambda: daemon("dhcp6")["running"], "kea-dhcp6 running", timeout=20)
        print("    kea-dhcp6 is running")
    except Failure as exc:
        print(f"    NOTICE: {exc} (not a failure; the config itself was accepted by `kea-dhcp6 -t`)")
        for e in [e for e in log_entries() if e["source"] == "dhcp6"][-5:]:
            print("      " + e["line"][:160])

    print("\nSMOKE TEST PASSED")


def main():
    try:
        run()
        return 0
    except Failure as exc:
        print(f"\nSMOKE TEST FAILED: {exc}", file=sys.stderr)
    except Exception as exc:                      # unexpected: still show the diagnostics
        print(f"\nSMOKE TEST ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        print("--- status ---\n" + json.dumps(status(), indent=2), file=sys.stderr)
        print("--- last 60 log lines ---", file=sys.stderr)
        for e in log_entries()[-60:]:
            print(f"[{e['source']}] {e['line']}", file=sys.stderr)
    except Exception as exc:
        print(f"(could not collect diagnostics: {exc})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
