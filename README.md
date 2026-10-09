# wifi-ap – Wi-Fi access point for SMHUB

A small app for **SMLIGHT SMHUB OS** that creates a 2.4 GHz Wi-Fi network from the hub, meant for connecting home-automation devices directly to the hub.

- **The hub uses Ethernet while the AP is on**: the hub's own Wi-Fi connection is disconnected when the AP is turned on and reconnected when it is turned off (the AIC8800 cannot run both reliably, see below). Before turning the AP on, the app asks for confirmation and shows whether a wired connection is detected.
- **DHCP and DNS** for the clients (dnsmasq from SMHUB OS) and **internet sharing with NAT** (optional).
- **WPA2, WPA2/WPA3, WPA3 or open**, hidden SSID, password generator.
- **Automatic channel**: scans and picks the least busy of channels 1, 6 and 11. Manual channel 1–13 is also possible.
- **QR code** to connect a phone, and a **list of connected devices** (name, MAC, IP, signal, connected time, traffic).
- The AP comes back after a reboot if it was enabled; a watchdog restarts it if hostapd stops.
- Shows up in the hub panel like any other app (sidebar + iframe), with a **Spanish / English** UI and light/dark theme.

## Requirements

- SMHUB with **SMHUB OS 1.0.x** (tested on a **SMHUB Nano MG24**, SMHUB OS 1.0.2, AIC8800 Wi-Fi).
- `hostapd` built for the hub (see below): the `wpa_supplicant` shipped with SMHUB OS has no AP mode and there is no `hostapd` package, so NetworkManager cannot create access points.
- Everything else is already in SMHUB OS: `iw`, `dnsmasq`, `iptables`, `nmcli`, `qrencode` and Python 3 (standard library only).

**AIC8800 limitations (SMHUB Nano):**

- **2.4 GHz only.** An AP on 5 GHz hangs the driver while the hub Wi-Fi is connected (the hardware watchdog reboots the hub), and with the hub Wi-Fi disconnected hostapd reports AP-ENABLED on channels 36–48 but the network is not visible.
- **AP + Wi-Fi client together is unreliable.** On the same 2.4 GHz channel the AP beacons are intermittent (the network appears and disappears, phones fail to connect), and while the hub is connected on 5 GHz the AP does not transmit at all. That is why the app always disconnects the hub Wi-Fi while the AP is on: the hub needs to be connected by **Ethernet** (required when internet sharing is on).

## How it works

| File | Purpose |
|---|---|
| `app/server.py` | HTTP server and API on port 8096. Creates `ap0` (`iw ... interface add ap0 type __ap`), keeps it unmanaged by NetworkManager, writes `hostapd.conf`, runs hostapd and dnsmasq, sets up NAT with dedicated iptables chains (`WIFIAP_*`), and cleans everything up when the AP is turned off |
| `app/index.html` | Web UI (ES/EN) |
| `app/hostapd`, `app/hostapd_cli` | Static hostapd 2.11 for riscv64 (not in the repository, built with `hostapd/build-hostapd.sh`) |
| `hostapd/` | Cross-compilation script and hostapd `.config` (nl80211, WPA2/WPA3-SAE, control interface; OpenSSL and libnl linked statically) |
| `control/` | opkg metadata: `control`, `openrc`, `schema.json` (`iframe_port: 8096`), `postinst`, `prerm`, `postrm` |
| `build.py` | Builds the `.ipk` with the Python standard library |
| `feed-index.py` | Rebuilds the local opkg feed index with every package in the feed, so other local apps stay listed |
| `install-root.sh` | Installs the package through a local opkg feed (`/opt/localfeed`) and restarts `smhub-services` so the app is registered in the panel |

API:

| Method | Path | Description |
|---|---|---|
| GET | `/api/state` | Configuration, AP status, uplink, connected clients, last hostapd log lines |
| POST | `/api/enable` | `{"enabled": true\|false}` |
| POST | `/api/config` | Save settings (applied immediately if the AP is on) |
| GET | `/api/qr.svg` | Wi-Fi QR code |
| GET | `/api/password` | Random password suggestion |

Settings are stored in `/opt/wifi-ap/data/config.json` (mode 600). Default network: `10.42.0.1/24`, DHCP range `.10`–`.250`.

## Build

1. Build hostapd on a Debian/Ubuntu x86_64 machine (cross-compiler, ~5 minutes):

   ```bash
   sh hostapd/build-hostapd.sh
   ```

   This leaves `app/hostapd` and `app/hostapd_cli`.

2. Copy the repository to the hub (e.g. `~/smhub-wifi-ap`) and, from the hub web console or SSH:

   ```bash
   cd ~/smhub-wifi-ap
   python3 build.py
   sudo sh install-root.sh
   ```

Reload the panel: **wifi-ap** appears under Apps and in the sidebar. It can also be opened at `http://<hub-ip>:8096`.

## Uninstall

```bash
sudo opkg remove wifi-ap
```

## License

MIT, see [LICENSE](LICENSE). The bundled hostapd binary is built from the unmodified hostapd 2.11 sources (BSD license, © Jouni Malinen and contributors), statically linked with OpenSSL 3 (Apache 2.0) and libnl (LGPL 2.1).

## Notes

- This is not an official SMLIGHT app. The app format was worked out by inspecting SMHUB OS 1.0.2 and may change in future releases.
- Turning the AP on or off never touches Ethernet. The hub's Wi-Fi connection is disconnected while the AP is on and restored when it is turned off (or when the service stops). The AP state survives reboots: if it was on, it comes back on and the hub Wi-Fi stays disconnected.
- With **netconfig** 1.0.5 or later, the hub Wi-Fi controls are blocked in netconfig while the AP is on, so both apps never fight over the radio.
