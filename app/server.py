#!/usr/bin/env python3
"""Wi-Fi access point for SMHUB OS.

Creates a virtual AP interface on the Wi-Fi radio (the hub stays connected as a client),
runs hostapd (bundled) + dnsmasq (DHCP/DNS) and shares the hub's internet with NAT.
Python 3 standard library only.
"""
import ipaddress, json, os, re, secrets, shutil, signal, string, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("WAP_PORT", "8096"))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("WAP_DATA", "/opt/wifi-ap/data")
RUN_DIR = os.environ.get("WAP_RUN", "/run/wifi-ap")
HOSTAPD = os.environ.get("WAP_HOSTAPD", os.path.join(HERE, "hostapd"))
HOSTAPD_CLI = os.environ.get("WAP_HOSTAPD_CLI", os.path.join(HERE, "hostapd_cli"))
AP_IF = os.environ.get("WAP_IFACE", "ap0")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CONF_FILE = os.path.join(RUN_DIR, "hostapd.conf")
HOSTAPD_LOG = os.path.join(RUN_DIR, "hostapd.log")
DNSMASQ_LOG = os.path.join(RUN_DIR, "dnsmasq.log")
LEASES = os.path.join(RUN_DIR, "dnsmasq.leases")
BAND_FILE = os.path.join(DATA_DIR, "band_lock.json")

CH_24 = list(range(1, 14))
# 2.4 GHz only: starting an AP on 5 GHz hangs the aic8800 driver (the hardware watchdog reboots the hub)
SECURITY = ("open", "wpa2", "wpa2wpa3", "wpa3")

lock = threading.RLock()
state = {"running": False, "error": None, "error_args": [], "warning": None, "ap": None,
         "hostapd": None, "dnsmasq": None, "fails": 0, "started": 0, "fwd_prev": None}


class ApError(Exception):
    """Error with a translation key and optional arguments."""
    def __init__(self, key, *args):
        super().__init__(key)
        self.key = key
        self.args_ = [str(a) for a in args]


def run(*cmd, check=True, timeout=20):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise ApError("err_cmd", " ".join(cmd[:4]), (p.stderr or p.stdout).strip()[:300])
    return p


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- radio info

def iw_devices():
    """Parse `iw dev` -> {ifname: {phy, type, mac, ssid, channel, freq, width}}."""
    out = run("iw", "dev", check=False).stdout
    devs, phy, cur = {}, None, None
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"phy#(\d+)", s)
        if m:
            phy = m.group(1)
            continue
        m = re.match(r"Interface (\S+)", s)
        if m:
            cur = devs.setdefault(m.group(1), {"phy": phy})
            continue
        if cur is None:
            continue
        if s.startswith("addr "):
            cur["mac"] = s.split()[1]
        elif s.startswith("type "):
            cur["type"] = s.split(None, 1)[1]
        elif s.startswith("ssid "):
            cur["ssid"] = s.split(None, 1)[1]
        elif s.startswith("channel "):
            m = re.match(r"channel (\d+) \((\d+) MHz\)(?:, width: (\d+) MHz)?", s)
            if m:
                cur["channel"], cur["freq"] = int(m.group(1)), int(m.group(2))
                cur["width"] = int(m.group(3)) if m.group(3) else None
    return devs


def client_iface(devs=None):
    devs = devs if devs is not None else iw_devices()
    for name, d in sorted(devs.items()):
        if name != AP_IF and d.get("type") == "managed":
            return name, d
    return None, None


def band_of(ch):
    return "5" if ch and ch > 14 else "2.4"


def default_ssid():
    _, d = client_iface()
    mac = (d or {}).get("mac", "00:00:00:00:00:00")
    return "SMHUB-" + mac.replace(":", "")[-4:].upper()


# ---------------------------------------------------------------- config

def gen_password():
    alphabet = string.ascii_letters + string.digits
    alphabet = "".join(c for c in alphabet if c not in "lIO0")
    return "".join(secrets.choice(alphabet) for _ in range(12))


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    d = {"enabled": False, "ssid": "", "password": "", "security": "wpa2", "hidden": False,
         "band": "2.4", "channel": 0, "country": "ES", "subnet": "10.42.0.1/24", "nat": True, "lock_24": True}
    d.update({k: v for k, v in c.items() if k in d})
    d["band"] = "2.4"
    if d["channel"] not in CH_24:
        d["channel"] = 0
    if not d["ssid"]:
        d["ssid"] = default_ssid()
    if not d["password"]:
        d["password"] = gen_password()
    return d


def save_config(c):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_FILE)


def validate(body, cur):
    c = dict(cur)
    ssid = str(body.get("ssid", c["ssid"]))
    if not 1 <= len(ssid.encode()) <= 32:
        raise ApError("err_ssid")
    c["ssid"] = ssid
    sec = body.get("security", c["security"])
    if sec not in SECURITY:
        raise ApError("err_security")
    c["security"] = sec
    pw = str(body.get("password", c["password"]))
    if sec != "open" and not (8 <= len(pw) <= 63 and all(32 <= ord(ch) < 127 for ch in pw)):
        raise ApError("err_password")
    c["password"] = pw
    c["hidden"] = bool(body.get("hidden", c["hidden"]))
    c["band"] = "2.4"
    try:
        ch = int(body.get("channel", c["channel"]) or 0)
    except (TypeError, ValueError):
        raise ApError("err_channel")
    if ch and ch not in CH_24:
        raise ApError("err_channel")
    c["channel"] = ch
    country = str(body.get("country", c["country"]) or "").upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ApError("err_country")
    c["country"] = country
    try:
        iface = ipaddress.ip_interface(str(body.get("subnet", c["subnet"])).strip())
    except ValueError:
        raise ApError("err_subnet")
    net = iface.network
    if iface.version != 4 or not net.is_private or not 16 <= net.prefixlen <= 28 \
            or iface.ip in (net.network_address, net.broadcast_address):
        raise ApError("err_subnet")
    for other in host_networks():
        if net.overlaps(other):
            raise ApError("err_overlap", str(other))
    c["subnet"] = str(iface)
    c["nat"] = bool(body.get("nat", c["nat"]))
    c["lock_24"] = bool(body.get("lock_24", c["lock_24"]))
    return c


def host_networks():
    """IPv4 networks on the hub's other interfaces (to avoid overlapping subnets)."""
    nets = []
    out = run("ip", "-4", "-o", "addr", "show", check=False).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] not in ("lo", AP_IF):
            try:
                nets.append(ipaddress.ip_interface(parts[3]).network)
            except ValueError:
                pass
    return nets


# ---------------------------------------------------------------- start / stop

def best_channel(client_name):
    """Least busy of 1/6/11 from a Wi-Fi scan (used when the hub is not a 2.4 GHz client)."""
    score = {1: 0.0, 6: 0.0, 11: 0.0}
    args = ["nmcli", "-t", "-f", "CHAN,SIGNAL", "device", "wifi", "list", "--rescan", "yes"]
    if client_name:
        args += ["ifname", client_name]
    out = run(*args, check=False, timeout=25).stdout
    seen = 0
    for line in out.splitlines():
        parts = line.split(":")
        try:
            ch, sig = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            continue
        if ch > 14:
            continue
        seen += 1
        for c in score:
            d = abs(c - ch)
            if d < 5:  # 20 MHz channels overlap up to 4 channels away
                score[c] += sig * (1 - d / 5.0)
    best = min(score, key=lambda c: (score[c], c != 6))
    log("channel scan:", seen, "networks, scores", score, "->", best)
    return best


def pick_channel(c, client, client_name=None):
    """-> (channel, mode) where mode is 'follows', 'best' or 'manual'."""
    cch = client.get("channel") if client else None
    if c["channel"]:
        return c["channel"], "manual"
    if cch and cch in CH_24:
        return cch, "follows"
    try:
        return best_channel(client_name), "best"
    except Exception as e:  # noqa
        log("channel scan failed:", e)
        return 6, "best"


def hostapd_conf(c, ch):
    lines = ["interface=%s" % AP_IF, "driver=nl80211", "ctrl_interface=%s" % RUN_DIR,
             "ctrl_interface_group=0", "ssid2=%s" % c["ssid"].encode().hex(), "utf8_ssid=1",
             "hw_mode=g", "channel=%d" % ch,
             "ieee80211n=1", "wmm_enabled=1", "auth_algs=1", "beacon_int=100",
             "ignore_broadcast_ssid=%d" % (1 if c["hidden"] else 0)]
    if c["country"]:
        lines += ["country_code=%s" % c["country"], "ieee80211d=1"]
    sec, pw = c["security"], c["password"]
    if sec == "wpa2":
        lines += ["wpa=2", "wpa_key_mgmt=WPA-PSK", "rsn_pairwise=CCMP", "wpa_passphrase=" + pw]
    elif sec == "wpa2wpa3":
        lines += ["wpa=2", "wpa_key_mgmt=WPA-PSK SAE", "rsn_pairwise=CCMP", "wpa_passphrase=" + pw,
                  "sae_password=" + pw, "ieee80211w=1", "sae_require_mfp=1"]
    elif sec == "wpa3":
        lines += ["wpa=2", "wpa_key_mgmt=SAE", "rsn_pairwise=CCMP", "sae_password=" + pw,
                  "ieee80211w=2", "sae_pwe=2"]
    return "\n".join(lines) + "\n"


def ensure_iface(client_name, client):
    devs = iw_devices()
    if AP_IF not in devs:
        if not client_name:
            raise ApError("err_no_radio")
        run("iw", "dev", client_name, "interface", "add", AP_IF, "type", "__ap")
        mac = client.get("mac")
        if mac:
            first = int(mac[:2], 16) | 0x02
            run("ip", "link", "set", AP_IF, "address", "%02x%s" % (first, mac[2:]), check=False)
    run("nmcli", "device", "set", AP_IF, "managed", "no", check=False)


def iptables_chain(table, chain, parent, rules):
    t = ["-t", table]
    run("iptables", *t, "-N", chain, check=False)
    run("iptables", *t, "-F", chain, check=False)
    for r in rules:
        run("iptables", *t, "-A", chain, *r)
    if run("iptables", *t, "-C", parent, "-j", chain, check=False).returncode != 0:
        run("iptables", *t, "-I", parent, "1", "-j", chain)


def iptables_clear():
    for table, chain, parent in (("nat", "WIFIAP_NAT", "POSTROUTING"), ("filter", "WIFIAP_FWD", "FORWARD"),
                                 ("filter", "WIFIAP_IN", "INPUT")):
        for _ in range(5):
            if run("iptables", "-t", table, "-D", parent, "-j", chain, check=False).returncode != 0:
                break
        run("iptables", "-t", table, "-F", chain, check=False)
        run("iptables", "-t", table, "-X", chain, check=False)


def set_forward(on):
    path = os.environ.get("WAP_FWD", "/proc/sys/net/ipv4/ip_forward")
    try:
        with open(path) as f:
            prev = f.read().strip()
        if on and state["fwd_prev"] is None:
            state["fwd_prev"] = prev
        val = "1" if on else (state["fwd_prev"] or prev)
        with open(path, "w") as f:
            f.write(val)
        if not on:
            state["fwd_prev"] = None
    except OSError:
        pass


def wait_for(path, pattern, proc, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with open(path, errors="replace") as f:
                txt = f.read()
            if re.search(pattern, txt):
                return True
        except OSError:
            pass
        if proc.poll() is not None:
            return False
        time.sleep(0.3)
    return False


def tail(path, n=15):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


# ---------------------------------------------------------------- client Wi-Fi band lock
# The AIC8800 cannot run the AP on 2.4 GHz while the client is connected on 5 GHz (the AP reports
# AP-ENABLED but the radio stays on the 5 GHz channel and no beacons are sent). While the AP is
# enabled, the hub's own Wi-Fi profiles are locked to 2.4 GHz; the original band is restored when
# the AP is turned off.

def wifi_profiles():
    out = run("nmcli", "-t", "-f", "UUID,TYPE", "connection", "show", check=False).stdout
    res = []
    for line in out.splitlines():
        uuid, _, typ = line.partition(":")
        if typ != "802-11-wireless":
            continue
        f = run("nmcli", "-t", "-f", "802-11-wireless.mode,802-11-wireless.band,connection.id",
                "connection", "show", uuid, check=False).stdout
        vals = dict(l.split(":", 1) for l in f.splitlines() if ":" in l)
        if vals.get("802-11-wireless.mode", "infrastructure") in ("infrastructure", ""):
            res.append({"uuid": uuid, "band": vals.get("802-11-wireless.band", ""),
                        "name": vals.get("connection.id", "")})
    return res


def load_band_lock():
    try:
        with open(BAND_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def lock_client_band(cname, client):
    """Lock Wi-Fi profiles to 2.4 GHz; reconnect if the hub is on 5 GHz. -> (cname, client)."""
    saved = load_band_lock()
    changed = False
    for p in wifi_profiles():
        if p["band"] != "bg":
            saved.setdefault(p["uuid"], p["band"])
            run("nmcli", "connection", "modify", p["uuid"], "802-11-wireless.band", "bg", check=False)
            changed = True
            log("locked Wi-Fi profile to 2.4 GHz:", p["name"])
    if changed:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(BAND_FILE, "w") as f:
            json.dump(saved, f)
    if cname and client and (client.get("channel") or 0) > 14:
        act = run("nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active", check=False).stdout
        uuid = next((l.split(":")[0] for l in act.splitlines() if l.endswith(":" + cname)), None)
        if uuid:
            log("hub Wi-Fi is on 5 GHz, reconnecting on 2.4 GHz")
            r = run("nmcli", "-w", "40", "connection", "up", uuid, check=False, timeout=50)
            if r.returncode != 0:
                # the network has no 2.4 GHz: give the profile its band back so the hub stays online
                orig = saved.pop(uuid, "")
                run("nmcli", "connection", "modify", uuid, "802-11-wireless.band", orig, check=False)
                with open(BAND_FILE, "w") as f:
                    json.dump(saved, f)
                run("nmcli", "-w", "40", "connection", "up", uuid, check=False, timeout=50)
                state["no24"] = True
                log("network has no 2.4 GHz, band restored")
            for _ in range(10):
                cname, client = client_iface()
                if (client or {}).get("channel"):
                    break
                time.sleep(2)
    return cname, client


def restore_client_band():
    saved = load_band_lock()
    for uuid, band in saved.items():
        run("nmcli", "connection", "modify", uuid, "802-11-wireless.band", band, check=False)
        log("restored Wi-Fi band of", uuid, "to", band or "auto")
    try:
        os.remove(BAND_FILE)
    except OSError:
        pass


def start_ap():
    with lock:
        stop_ap(quiet=True)
        c = load_config()
        os.makedirs(RUN_DIR, exist_ok=True)
        if not os.access(HOSTAPD, os.X_OK):
            raise ApError("err_no_hostapd", HOSTAPD)
        cname, client = client_iface()
        if c["lock_24"]:
            cname, client = lock_client_band(cname, client)
        elif load_band_lock():
            restore_client_band()
        ch, mode = pick_channel(c, client, cname)
        follows = mode == "follows"
        cch = (client or {}).get("channel")
        state["warning"] = None
        if ch not in CH_24:
            raise ApError("err_channel")
        if cch and cch > 14:
            state["warning"] = ["warn_5ghz", str(ch), str(cch)]
        elif cch and ch != cch:
            state["warning"] = ["warn_channel", str(ch), str(cch)]
        ensure_iface(cname, client or {})
        iface = ipaddress.ip_interface(c["subnet"])
        net = iface.network
        hosts = list(net.hosts())
        others = [h for h in hosts if h != iface.ip]
        lo, hi = others[min(8, len(others) - 1)], others[max(-5, -len(others))]

        with open(CONF_FILE, "w") as f:
            f.write(hostapd_conf(c, ch))
        os.chmod(CONF_FILE, 0o600)
        run("ip", "link", "set", AP_IF, "down", check=False)
        run("ip", "addr", "flush", "dev", AP_IF, check=False)
        hlog = open(HOSTAPD_LOG, "w")
        hp = subprocess.Popen([HOSTAPD, CONF_FILE], stdout=hlog, stderr=subprocess.STDOUT,
                              start_new_session=True)
        state["hostapd"] = hp
        if not wait_for(HOSTAPD_LOG, r"AP-ENABLED", hp, 25):
            lines = tail(HOSTAPD_LOG, 6)
            stop_ap(quiet=True)
            raise ApError("err_hostapd", " / ".join(lines)[-400:])

        run("ip", "addr", "add", str(iface), "dev", AP_IF, check=False)
        run("ip", "link", "set", AP_IF, "up", check=False)

        opts = ["dnsmasq", "--keep-in-foreground", "--conf-file=/dev/null", "--user=root",
                "--interface=" + AP_IF, "--bind-interfaces", "--except-interface=lo",
                "--listen-address=%s" % iface.ip,
                "--dhcp-range=%s,%s,%s,12h" % (lo, hi, net.netmask),
                "--dhcp-leasefile=" + LEASES, "--dhcp-authoritative", "--pid-file=",
                "--log-facility=" + DNSMASQ_LOG, "--log-dhcp"]
        if c["nat"]:
            opts += ["--dhcp-option=option:router,%s" % iface.ip]
        else:
            opts += ["--dhcp-option=option:router"]
        dp = subprocess.Popen(opts + ["--dhcp-option=option:dns-server,%s" % iface.ip],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        time.sleep(1.2)
        if dp.poll() is not None:
            # port 53 busy: DHCP only, hand out the hub's upstream DNS servers
            dns = upstream_dns() or ["1.1.1.1"]
            dp = subprocess.Popen(opts + ["--port=0", "--dhcp-option=option:dns-server," + ",".join(dns)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
            time.sleep(1.2)
            if dp.poll() is not None:
                err = (dp.stderr.read() or b"").decode(errors="replace").strip()
                stop_ap(quiet=True)
                raise ApError("err_dnsmasq", err[:300])
        state["dnsmasq"] = dp

        iptables_chain("filter", "WIFIAP_IN", "INPUT", [["-i", AP_IF, "-j", "ACCEPT"]])
        if c["nat"]:
            set_forward(True)
            iptables_chain("nat", "WIFIAP_NAT", "POSTROUTING",
                           [["-s", str(net), "!", "-d", str(net), "-j", "MASQUERADE"]])
            iptables_chain("filter", "WIFIAP_FWD", "FORWARD",
                           [["-i", AP_IF, "-j", "ACCEPT"],
                            ["-o", AP_IF, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"]])
        state.update(running=True, error=None, error_args=[], started=time.time(),
                     ap={"iface": AP_IF, "channel": ch, "band": band_of(ch), "follows": follows, "mode": mode,
                         "ip": str(iface.ip), "net": str(net), "client_iface": cname})
        log("AP started on channel", ch, "ssid", c["ssid"])


def stop_ap(quiet=False):
    with lock:
        for key in ("dnsmasq", "hostapd"):
            p = state.get(key)
            if p and p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait(5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except OSError:
                        pass
            state[key] = None
        iptables_clear()
        set_forward(False)
        if AP_IF in iw_devices():
            run("ip", "addr", "flush", "dev", AP_IF, check=False)
            run("ip", "link", "set", AP_IF, "down", check=False)
            run("iw", "dev", AP_IF, "del", check=False)
        was = state["running"]
        state.update(running=False, ap=None)
        if was and not quiet:
            log("AP stopped")


def upstream_dns():
    try:
        with open("/etc/resolv.conf") as f:
            return [l.split()[1] for l in f if l.startswith("nameserver") and len(l.split()) > 1
                    and not l.split()[1].startswith("127.")]
    except OSError:
        return []


# ---------------------------------------------------------------- clients

def clients():
    if not state["running"]:
        return []
    leases = {}
    try:
        with open(LEASES) as f:
            for l in f:
                p = l.split()
                if len(p) >= 4:
                    leases[p[1].lower()] = {"ip": p[2], "name": "" if p[3] == "*" else p[3]}
    except OSError:
        pass
    out = run(HOSTAPD_CLI, "-p", RUN_DIR, "-i", AP_IF, "all_sta", check=False, timeout=5).stdout
    res, cur = [], None
    for line in out.splitlines():
        line = line.strip()
        if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", line, re.I):
            cur = {"mac": line.lower()}
            cur.update(leases.get(line.lower(), {"ip": "", "name": ""}))
            res.append(cur)
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            if k in ("signal", "connected_time", "rx_bytes", "tx_bytes", "inactive_msec"):
                try:
                    cur[k] = int(v.split()[0])
                except ValueError:
                    pass
    return res


def get_state():
    c = load_config()
    cname, client = client_iface()
    hp = state.get("hostapd")
    return {"config": c, "running": state["running"] and bool(hp and hp.poll() is None),
            "ap": state["ap"], "error": state["error"], "error_args": state["error_args"],
            "warning": state["warning"],
            "client": {"iface": cname, "ssid": (client or {}).get("ssid"),
                       "channel": (client or {}).get("channel")} if cname else None,
            "clients": clients(), "hostapd_found": os.access(HOSTAPD, os.X_OK),
            "channels": {"2.4": CH_24}, "log": tail(HOSTAPD_LOG, 12)}


def qr_svg():
    c = load_config()
    esc = lambda s: re.sub(r'([\\;,:"])', r"\\\1", s)
    t = {"open": "nopass", "wpa3": "SAE"}.get(c["security"], "WPA")
    payload = "WIFI:T:%s;S:%s;" % (t, esc(c["ssid"]))
    if c["security"] != "open":
        payload += "P:%s;" % esc(c["password"])
    if c["hidden"]:
        payload += "H:true;"
    payload += ";"
    if not shutil.which("qrencode"):
        raise ApError("err_qr")
    return run("qrencode", "-t", "SVG", "-m", "2", "-o", "-", payload).stdout.encode()


# ---------------------------------------------------------------- watchdog

def set_error(e):
    state["error"], state["error_args"] = e.key, e.args_
    log("error", e.key, *e.args_)


def try_start():
    try:
        start_ap()
        state["fails"] = 0
    except ApError as e:
        state["fails"] += 1
        set_error(e)
    except Exception as e:  # noqa
        state["fails"] += 1
        set_error(ApError("err_cmd", "start", str(e)))


def watchdog():
    # give NetworkManager time to bring the client Wi-Fi up so the AP can follow its channel
    if load_config()["enabled"]:
        end = time.time() + 45
        while time.time() < end and not (client_iface()[1] or {}).get("channel"):
            time.sleep(3)
        try_start()
    while True:
        time.sleep(10)
        with lock:
            c = load_config()
            if not c["enabled"]:
                continue
            hp = state.get("hostapd")
            dead = not state["running"] or not hp or hp.poll() is not None
            moved = False
            ap = state["ap"]
            cch = (client_iface()[1] or {}).get("channel")
            if not dead and c["lock_24"] and cch and cch > 14 and not state.get("no24"):
                moved = True  # the hub went back to 5 GHz (e.g. a new profile): lock and reconnect
            if not dead and ap and ap.get("mode") != "manual":
                # follow the client channel; also switch to it if the hub joins a 2.4 GHz network later
                moved = bool(cch and cch in CH_24 and cch != ap["channel"])
            if dead or moved:
                if dead and state["fails"] >= 5 and time.time() - state.get("last_try", 0) < 300:
                    continue
                state["last_try"] = time.time()
                log("restarting AP", "(client channel changed)" if moved else "(not running)")
                try_start()


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, e):
        self.send(400, {"error": e.key, "args": e.args_})

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send(200, get_state())
            if path == "/api/qr.svg":
                return self.send(200, qr_svg(), "image/svg+xml")
            if path == "/api/password":
                return self.send(200, {"password": gen_password()})
            self.send(404, {"error": "not_found"})
        except ApError as e:
            self.fail(e)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self.send(400, {"error": "err_json", "args": []})
        try:
            if path == "/api/config":
                with lock:
                    c = validate(body, load_config())
                    save_config(c)
                    if c["enabled"]:
                        state["fails"] = 0
                        state["no24"] = False
                        try:
                            start_ap()
                        except ApError as e:
                            set_error(e)
                            raise
                return self.send(200, get_state())
            if path == "/api/enable":
                with lock:
                    c = load_config()
                    c["enabled"] = bool(body.get("enabled"))
                    save_config(c)
                    state["fails"] = 0
                    state["no24"] = False
                    if c["enabled"]:
                        try:
                            start_ap()
                        except ApError as e:
                            set_error(e)
                            raise
                    else:
                        stop_ap()
                        restore_client_band()
                        state.update(error=None, error_args=[], warning=None)
                return self.send(200, get_state())
            self.send(404, {"error": "not_found"})
        except ApError as e:
            self.fail(e)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RUN_DIR, exist_ok=True)
    c = load_config()
    if not os.path.exists(CONFIG_FILE):
        save_config(c)  # keep the generated SSID/password stable
    # clean leftovers from a previous run
    run("pkill", "-f", "dnsmasq.*--interface=" + AP_IF, check=False)
    run("pkill", "-f", CONF_FILE, check=False)
    stop_ap(quiet=True)

    def bye(*_):
        stop_ap()
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    threading.Thread(target=watchdog, daemon=True).start()
    log("wifi-ap listening on port", PORT)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
