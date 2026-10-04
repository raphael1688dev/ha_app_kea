"""Owns the Kea daemons: stage + test configs, persist settings, reload/restart,
capture daemon output, and manipulate lease files."""
import collections
import ipaddress
import json
import os
import shutil
import signal
import subprocess
import threading
import time

import leases as lease_files
import settings as cfg

DAEMONS = {
    "dhcp4": {"binary": "kea-dhcp4", "conf": "kea-dhcp4.conf", "leases": "kea-leases4.csv"},
    "dhcp6": {"binary": "kea-dhcp6", "conf": "kea-dhcp6.conf", "leases": "kea-leases6.csv"},
}
PERSISTENT_MOUNT = "/config"   # where the `app_config` map is mounted in the container
LOG_LIMIT = 3000
# After applying settings we watch the daemons for a moment: Kea can accept a config file in `-t` and still
# fail for real (for example an interface that does not exist), and a reload failure only shows up in its log.
SETTLE_SECONDS = 1.5
RELOAD_GRACE = 2.0       # a daemon younger than this may not have installed its SIGHUP handler yet
FAIL_MARKERS = ("RECONFIGURATION_FAIL", "CONFIG_LOAD_FAIL", "INIT_FAIL")
LOG_SOURCES = ("addon", "dhcp4", "dhcp6")


def log(message):
    print(f"[kea-app] {message}", flush=True)


def load_options(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        log(f"cannot read {path}, using default options")
        return {}


class KeaManager:
    def __init__(self, options, conf_dir, data_dir):
        self.options = options
        self.conf_dir = conf_dir
        self.data_dir = data_dir
        # The app options only seed the global settings; once settings.json
        # has a "global" section, the Web UI is the source of truth.
        self.default_global = cfg.default_global(
            str(options.get("interface") or "eth0"), bool(options.get("enable_dhcp6")))
        self.settings_path = os.path.join(data_dir, "settings.json")

        self.lock = threading.RLock()
        self.procs = {}
        self.info = {name: {"started_at": None, "exit_code": None} for name in DAEMONS}
        self.last_error = None
        self.settings = None
        self._stopping = False
        self.settle_seconds = SETTLE_SECONDS
        self.reload_grace = RELOAD_GRACE

        self._logs = collections.deque(maxlen=LOG_LIMIT)
        self._log_seq = 0
        self._log_lock = threading.Lock()

        for path in (self.conf_dir, self.data_dir, "/run/kea"):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                pass  # e.g. /run is not writable when developing locally

        self.env = dict(os.environ)
        self.env.update({
            "KEA_DHCP_DATA_DIR": self.data_dir,
            "KEA_PIDFILE_DIR": "/run/kea",
            "KEA_LOCKFILE_DIR": "/run/kea",
            "KEA_LOG_FILE_DIR": self.data_dir,
        })

    # -------------------------------------------------------------- globals
    @property
    def interface(self):
        return self._global_of(self.settings)["interface"]

    @property
    def enabled(self):
        return self._enabled_for(self.settings)

    def _global_of(self, data):
        return (data or {}).get("global") or self.default_global

    def _enabled_for(self, data):
        return ["dhcp4"] + (["dhcp6"] if self._global_of(data)["enable_dhcp6"] else [])

    # ---------------------------------------------------------------- logs
    def _add_log(self, source, line):
        with self._log_lock:
            self._log_seq += 1
            self._logs.append({"seq": self._log_seq, "ts": time.time(),
                               "source": source, "line": line})
        label = "kea-app" if source == "addon" else DAEMONS[source]["binary"]
        print(f"[{label}] {line}", flush=True)

    def _log(self, message):
        self._add_log("addon", message)

    def get_logs(self, since=0, source=None, limit=500):
        limit = max(1, min(int(limit), LOG_LIMIT))
        with self._log_lock:
            entries = [e for e in self._logs
                       if e["seq"] > since and (not source or e["source"] == source)]
            last = self._log_seq
        return {"entries": entries[-limit:], "last_seq": last}

    def _pump(self, name, proc):
        try:
            for line in proc.stdout:
                self._add_log(name, line.rstrip("\r\n"))
        finally:
            proc.stdout.close()

    # --------------------------------------------------------------- paths
    def lease_path(self, name):
        return os.path.join(self.data_dir, DAEMONS[name]["leases"])

    def _conf_path(self, name, suffix=""):
        return os.path.join(self.conf_dir, DAEMONS[name]["conf"] + suffix)

    def _binary(self, name):
        return shutil.which(DAEMONS[name]["binary"])

    # --------------------------------------------------------- persistence
    def _default_settings(self):
        g = self.default_global
        return cfg.default_settings(g["interface"], g["enable_dhcp6"])

    def load_settings(self):
        try:
            with open(self.settings_path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            self._log("settings.json not found, using defaults")
            return self._default_settings()
        except (OSError, ValueError) as exc:
            self._log(f"failed to read settings.json: {exc}; using defaults")
            return self._default_settings()
        try:
            cleaned, errors = cfg.validate(raw, self.default_global)
        except Exception as exc:                       # a malformed file must not crash-loop the app
            self._log(f"settings.json could not be understood ({type(exc).__name__}: {exc}); using defaults")
            return self._default_settings()
        if errors:
            self._log("settings.json has problems: " + "; ".join(errors))
        return cleaned if cleaned else self._default_settings()

    def _save_settings(self, data):
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.settings_path)

    # ------------------------------------------------------- render / test
    def _render(self, name, data):
        if name == "dhcp4":
            return cfg.render_dhcp4(data, self.lease_path(name))
        return cfg.render_dhcp6(data, self.lease_path(name))

    def _stage_and_test(self, data):
        """Write *.new files and run `kea -t`. Returns a list of problems."""
        problems = []
        staged = []
        for name in self._enabled_for(data):
            new_path = self._conf_path(name, ".new")
            with open(new_path, "w", encoding="utf-8") as fh:
                json.dump(self._render(name, data), fh, indent=2)
            staged.append(new_path)
            binary = self._binary(name)
            if not binary:
                self._log(f"{DAEMONS[name]['binary']} not found, skipping config test")
                continue
            try:
                result = subprocess.run(
                    [binary, "-t", new_path], env=self.env,
                    capture_output=True, text=True, timeout=30)
            except (subprocess.TimeoutExpired, OSError) as exc:
                problems.append(f"{DAEMONS[name]['binary']} could not test the configuration: {exc}")
                continue
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip().splitlines()
                tail = "\n".join(output[-12:])
                problems.append(f"{DAEMONS[name]['binary']} rejected the configuration:\n{tail}")
        if problems:
            for path in staged:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return problems

    def _commit_staged(self, data):
        for name in self._enabled_for(data):
            os.replace(self._conf_path(name, ".new"), self._conf_path(name))

    # ------------------------------------------------------------- process
    def _running(self, name):
        proc = self.procs.get(name)
        return bool(proc and proc.poll() is None)

    def _start(self, name):
        binary = self._binary(name)
        if not binary:
            self._log(f"{DAEMONS[name]['binary']} not found, cannot start (development mode)")
            return
        self._log(f"starting {DAEMONS[name]['binary']} (interface={self.interface})")
        proc = subprocess.Popen(
            [binary, "-c", self._conf_path(name)], env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self.procs[name] = proc
        self.info[name] = {"started_at": time.time(), "exit_code": None}
        threading.Thread(target=self._pump, args=(name, proc), daemon=True).start()

    def _stop(self, name):
        proc = self.procs.pop(name, None)
        if not proc or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _restart_all(self):
        for name in DAEMONS:
            self._stop(name)
        for name in self.enabled:
            self._start(name)

    def _activate(self, previous):
        """Bring the daemons in line with the (already committed) settings.

        Running daemons get SIGHUP so Kea re-reads its config without dropping service. A changed interface,
        a daemon that is not running, or one that started a moment ago (its SIGHUP handler may not be installed
        yet, and the default action of SIGHUP is to terminate) gets a real (re)start. Turning DHCPv6 on or off
        only starts or stops kea-dhcp6. Returns which daemons were reloaded / restarted / started / stopped.
        """
        wanted = self.enabled
        interface_changed = (previous is None
                             or self._global_of(previous)["interface"] != self.interface)
        changes = {"reloaded": [], "restarted": [], "started": [], "stopped": []}
        now = time.time()
        for name in DAEMONS:
            was_running = self._running(name)
            started_at = self.info[name]["started_at"] or 0
            if name not in wanted:
                if was_running:
                    changes["stopped"].append(name)
                self._stop(name)
            elif was_running and not interface_changed and now - started_at >= self.reload_grace:
                self._log(f"reloading {DAEMONS[name]['binary']} configuration (SIGHUP)")
                self.procs[name].send_signal(signal.SIGHUP)
                changes["reloaded"].append(name)
            else:
                self._stop(name)
                self._start(name)
                changes["restarted" if was_running else "started"].append(name)
        return changes

    def _snapshot(self):
        """Current settings.json and generated configs, so a failed apply can be undone."""
        def read(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                return None
        return {"settings": read(self.settings_path),
                "confs": {name: read(self._conf_path(name)) for name in DAEMONS}}

    def _restore(self, path, text):
        if text is None:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        tmp = path + ".restore"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def _rollback(self, previous, snapshot):
        self._log("restoring the previous configuration")
        self._restore(self.settings_path, snapshot["settings"])
        for name, text in snapshot["confs"].items():
            self._restore(self._conf_path(name), text)
        self.settings = previous
        self._restart_all()
        self.last_error = None

    def _check_daemons(self, mark):
        problems = []
        for name in self.enabled:
            if self._binary(name) is None:
                continue                                   # development machine without Kea
            proc = self.procs.get(name)
            if proc is None or proc.poll() is not None:
                code = proc.poll() if proc else None
                tail = [e["line"] for e in self.get_logs(since=mark, source=name, limit=6)["entries"]]
                problems.append(f"{DAEMONS[name]['binary']} stopped right after the new configuration was applied"
                                f" (exit code {code})." + ("\n" + "\n".join(tail) if tail else ""))
        if not problems:
            bad = [e["line"] for e in self.get_logs(since=mark, limit=LOG_LIMIT)["entries"]
                   if e["source"] in DAEMONS and any(m in e["line"] for m in FAIL_MARKERS)]
            if bad:
                problems.append("Kea reported a problem with the new configuration:\n" + "\n".join(bad[:4]))
        return problems

    def _settle(self, mark):
        """Watch the daemons for a moment after an apply; return the problems found (empty list = fine)."""
        deadline = time.time() + self.settle_seconds
        while True:
            problems = self._check_daemons(mark)
            if problems or time.time() >= deadline:
                return problems
            time.sleep(0.2)

    def watch(self):
        """Background loop: notice daemons that died on their own."""
        while not self._stopping:
            time.sleep(2)
            with self.lock:
                for name, proc in list(self.procs.items()):
                    code = proc.poll()
                    if code is not None and self.info[name]["exit_code"] is None:
                        self.info[name]["exit_code"] = code
                        self.last_error = (
                            f"{DAEMONS[name]['binary']} exited unexpectedly (exit code {code}). "
                            "Fix the settings, then use \"Save & apply\" or \"Restart Kea\".")
                        self._log(self.last_error)

    # ----------------------------------------------------------- public API
    def startup(self):
        with self.lock:
            if not self.storage_persistent():
                self._log("WARNING: /config is not mounted. Settings and leases will be lost when "
                          "the app restarts. The Supervisor may be older than 2026.07 and "
                          "not know the 'app_config' map type; update Home Assistant.")
            self.settings = self.load_settings()
            problems = self._stage_and_test(self.settings)
            if problems:
                self.last_error = "\n".join(problems)
                self._log("Configuration test failed at startup; Kea was not started. "
                          "Fix the settings in the Web UI.\n" + self.last_error)
                return
            self._commit_staged(self.settings)
            if "dhcp6" not in self.enabled:
                self._log("DHCPv6 is disabled, not starting kea-dhcp6")
            self._restart_all()
            self.last_error = None

    def apply(self, raw):
        cleaned, errors = cfg.validate(raw, self._global_of(self.settings))
        if errors:
            return {"ok": False, "errors": errors}
        with self.lock:
            problems = self._stage_and_test(cleaned)
            if problems:
                return {"ok": False, "errors": problems}
            snapshot = self._snapshot()
            previous = self.settings
            self._save_settings(cleaned)
            self._commit_staged(cleaned)
            self.settings = cleaned
            with self._log_lock:
                mark = self._log_seq
            changes = self._activate(previous)
            failures = self._settle(mark)
            if failures:
                self._rollback(previous, snapshot)
                return {"ok": False, "errors": failures + ["The previous configuration was restored."]}
            self.last_error = None
            action = "restarted" if changes["restarted"] or "dhcp4" in changes["started"] else "reloaded"
            return {"ok": True, "settings": cleaned, "action": action, "changes": changes,
                    "status": self.status()}

    def restart(self):
        with self.lock:
            if self.settings is None:
                self.settings = self.load_settings()
            problems = self._stage_and_test(self.settings)
            if problems:
                self.last_error = "\n".join(problems)
                return {"ok": False, "errors": problems}
            self._commit_staged(self.settings)
            self._restart_all()
            self.last_error = None
            return {"ok": True, "status": self.status()}

    def import_preview(self, raw):
        """Validate/migrate an uploaded settings file without applying it."""
        cleaned, errors = cfg.validate(raw, self._global_of(self.settings))
        return {"ok": not errors, "errors": errors, "settings": cleaned if not errors else None}

    def delete_lease(self, family, address):
        """Remove a lease by appending a lifetime-0 row while the daemon is stopped."""
        if family not in DAEMONS:
            return {"ok": False, "errors": ["unknown address family"]}
        try:
            ip = ipaddress.ip_address(str(address).strip())
        except ValueError:
            return {"ok": False, "errors": ["invalid IP address"]}
        if ip.version != (4 if family == "dhcp4" else 6):
            return {"ok": False, "errors": ["address does not match the address family"]}
        address = str(ip)
        with self.lock:
            path = self.lease_path(family)
            fieldnames, row = lease_files.find_latest_row(path, address)
            if row is None or str(row.get("valid_lifetime", "")).strip() == "0":
                return {"ok": False, "errors": [f"no lease found for {address}"]}
            was_running = self._running(family)
            error = None
            self._stop(family)
            try:
                lease_files.append_deleted_row(path, fieldnames, row)
                self._log(f"deleted lease {address}")
            except OSError as exc:
                error = f"could not update the lease file: {exc}"
            if was_running and family in self.enabled:
                self._start(family)          # do not resurrect a daemon that was stopped on purpose or failed
            if error:
                return {"ok": False, "errors": [error]}
            return {"ok": True}

    def list_interfaces(self):
        names, default = [], None
        try:
            names = sorted(n for n in os.listdir("/sys/class/net") if n != "lo")
        except OSError:
            pass
        try:
            with open("/proc/net/route", encoding="utf-8") as fh:
                for line in list(fh)[1:]:
                    parts = line.split()
                    if len(parts) > 2 and parts[1] == "00000000":
                        default = parts[0]
                        break
        except OSError:
            pass
        return {"interfaces": names, "default": default}

    def storage_persistent(self):
        """False when /config is not a mount, i.e. settings and leases would sit on
        the container's ephemeral layer and vanish on restart (the Supervisor skips
        map types it does not know, such as `app_config` before 2026.07)."""
        data = os.path.abspath(self.data_dir)
        if data == PERSISTENT_MOUNT or data.startswith(PERSISTENT_MOUNT + os.sep):
            return os.path.ismount(PERSISTENT_MOUNT)
        return True

    def status(self):
        with self.lock:
            enabled = self.enabled
            daemons = {}
            for name in DAEMONS:
                proc = self.procs.get(name)
                running = bool(proc and proc.poll() is None)
                daemons[name] = {
                    "enabled": name in enabled,
                    "installed": self._binary(name) is not None,
                    "running": running,
                    "pid": proc.pid if running else None,
                    "started_at": self.info[name]["started_at"] if running else None,
                    "exit_code": self.info[name]["exit_code"],
                }
            return {
                "interface": self.interface,
                "enable_dhcp6": "dhcp6" in enabled,
                "daemons": daemons,
                "last_error": self.last_error,
                "storage_persistent": self.storage_persistent(),
            }

    def shutdown(self):
        self._stopping = True
        with self.lock:
            for name in list(self.procs):
                self._stop(name)
