"""Owns the Kea daemons: stage + test configs, persist settings, (re)start."""
import json
import os
import shutil
import subprocess
import threading
import time

import settings as cfg

DAEMONS = {
    "dhcp4": {"binary": "kea-dhcp4", "conf": "kea-dhcp4.conf", "leases": "kea-leases4.csv"},
    "dhcp6": {"binary": "kea-dhcp6", "conf": "kea-dhcp6.conf", "leases": "kea-leases6.csv"},
}


def log(message):
    print(f"[kea-addon] {message}", flush=True)


def load_options(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        log(f"無法讀取 {path},使用預設選項")
        return {}


class KeaManager:
    def __init__(self, options, conf_dir, data_dir):
        self.options = options
        self.conf_dir = conf_dir
        self.data_dir = data_dir
        self.interface = str(options.get("interface") or "eth0")
        self.enabled = ["dhcp4"] + (["dhcp6"] if options.get("enable_dhcp6") else [])
        self.settings_path = os.path.join(data_dir, "settings.json")

        self.lock = threading.RLock()
        self.procs = {}
        self.info = {name: {"started_at": None, "exit_code": None} for name in DAEMONS}
        self.last_error = None
        self.settings = None
        self._stopping = False

        for path in (self.conf_dir, self.data_dir, "/run/kea"):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                pass  # e.g. /run is not writable when developing locally

        self.env = dict(os.environ)
        self.env.update({
            "KEA_LEASE_DIR": self.data_dir,
            "KEA_PIDFILE_DIR": "/run/kea",
            "KEA_LOCKFILE_DIR": "/run/kea",
            "KEA_LOG_FILE_DIR": self.data_dir,
        })

    # ------------------------------------------------------------------ paths
    def lease_path(self, name):
        return os.path.join(self.data_dir, DAEMONS[name]["leases"])

    def _conf_path(self, name, suffix=""):
        return os.path.join(self.conf_dir, DAEMONS[name]["conf"] + suffix)

    def _binary(self, name):
        return shutil.which(DAEMONS[name]["binary"])

    # ----------------------------------------------------------- persistence
    def load_settings(self):
        try:
            with open(self.settings_path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            log("找不到 settings.json,建立預設設定")
            return cfg.default_settings()
        except (OSError, ValueError) as exc:
            log(f"讀取 settings.json 失敗:{exc};改用預設設定")
            return cfg.default_settings()
        cleaned, errors = cfg.validate(raw)
        if errors:
            log("settings.json 內容有誤:" + "; ".join(errors))
        return cleaned if cleaned else cfg.default_settings()

    def _save_settings(self, data):
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.settings_path)

    # ----------------------------------------------------------- render/test
    def _render(self, name, data):
        if name == "dhcp4":
            return cfg.render_dhcp4(data, self.interface, self.lease_path(name))
        return cfg.render_dhcp6(data, self.interface, self.lease_path(name))

    def _stage_and_test(self, data):
        """Write *.new files and run `kea -t`. Returns a list of problems."""
        problems = []
        staged = []
        for name in self.enabled:
            new_path = self._conf_path(name, ".new")
            with open(new_path, "w", encoding="utf-8") as fh:
                json.dump(self._render(name, data), fh, indent=2)
            staged.append(new_path)
            binary = self._binary(name)
            if not binary:
                log(f"找不到 {DAEMONS[name]['binary']},略過語法檢查")
                continue
            result = subprocess.run(
                [binary, "-t", new_path], env=self.env,
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip().splitlines()
                tail = "\n".join(output[-12:])
                problems.append(f"{DAEMONS[name]['binary']} 設定檢查失敗:\n{tail}")
        if problems:
            for path in staged:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return problems

    def _commit_staged(self):
        for name in self.enabled:
            os.replace(self._conf_path(name, ".new"), self._conf_path(name))

    # --------------------------------------------------------------- process
    def _start(self, name):
        binary = self._binary(name)
        if not binary:
            log(f"找不到 {DAEMONS[name]['binary']},無法啟動(開發模式)")
            return
        conf = self._conf_path(name)
        log(f"啟動 {DAEMONS[name]['binary']} (interface={self.interface})")
        self.procs[name] = subprocess.Popen([binary, "-c", conf], env=self.env)
        self.info[name] = {"started_at": time.time(), "exit_code": None}

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
                            f"{DAEMONS[name]['binary']} 已異常結束 (exit code {code})。"
                            "請檢查設定後按「儲存並套用」或「重新啟動」。")
                        log(self.last_error)

    # ------------------------------------------------------------ public API
    def startup(self):
        with self.lock:
            self.settings = self.load_settings()
            problems = self._stage_and_test(self.settings)
            if problems:
                self.last_error = "\n".join(problems)
                log("啟動時設定檢查失敗,Kea 未啟動。請從 Web UI 修正設定。\n" + self.last_error)
                return
            self._commit_staged()
            self._restart_all()
            self.last_error = None

    def apply(self, raw):
        cleaned, errors = cfg.validate(raw)
        if errors:
            return {"ok": False, "errors": errors}
        with self.lock:
            problems = self._stage_and_test(cleaned)
            if problems:
                return {"ok": False, "errors": problems}
            self._save_settings(cleaned)
            self._commit_staged()
            self.settings = cleaned
            self._restart_all()
            self.last_error = None
            return {"ok": True, "settings": cleaned, "status": self.status()}

    def restart(self):
        with self.lock:
            if self.settings is None:
                self.settings = self.load_settings()
            problems = self._stage_and_test(self.settings)
            if problems:
                self.last_error = "\n".join(problems)
                return {"ok": False, "errors": problems}
            self._commit_staged()
            self._restart_all()
            self.last_error = None
            return {"ok": True, "status": self.status()}

    def status(self):
        with self.lock:
            daemons = {}
            for name in DAEMONS:
                proc = self.procs.get(name)
                running = bool(proc and proc.poll() is None)
                daemons[name] = {
                    "enabled": name in self.enabled,
                    "installed": self._binary(name) is not None,
                    "running": running,
                    "pid": proc.pid if running else None,
                    "started_at": self.info[name]["started_at"] if running else None,
                    "exit_code": self.info[name]["exit_code"],
                }
            return {
                "interface": self.interface,
                "enable_dhcp6": "dhcp6" in self.enabled,
                "daemons": daemons,
                "last_error": self.last_error,
            }

    def shutdown(self):
        self._stopping = True
        with self.lock:
            for name in list(self.procs):
                self._stop(name)
