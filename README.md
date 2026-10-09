# wifi-ap – Wi-Fi access point for SMHUB

A small app for **SMLIGHT SMHUB OS** that creates a Wi-Fi network from the hub **while it stays connected to its own network** (Ethernet or Wi-Fi client). Meant for connecting home-automation devices directly to the hub.

- **AP + client at the same time**: a virtual `ap0` interface on the same radio; the hub keeps its Wi-Fi/Ethernet uplink.
- **DHCP and DNS** for the clients (dnsmasq from SMHUB OS) and **internet sharing with NAT** (optional).
- **WPA2, WPA2/WPA3, WPA3 or open**, hidden SSID, password generator.
- **Automatic channel**: if the hub is connected to a 2.4 GHz Wi-Fi, the AP uses the same channel (the radio is shared, so this is the most stable option) and follows it if it changes; otherwise it scans and picks the least busy of channels 1, 6 and 11. Manual channel 1–13 is also possible.
- **QR code** to connect a phone, and a **list of connected devices** (name, MAC, IP, signal, connected time, traffic).
- Keeps the hub's own Wi-Fi on 2.4 GHz while the AP is on (required by the AIC8800 chip), restoring the original band afterwards.
- The AP comes back after a reboot if it was enabled; a watchdog restarts it if hostapd stops.
- Shows up in the hub panel like any other app (sidebar + iframe), with a **Spanish / English** UI and light/dark theme.

## Requirements

- SMHUB with **SMHUB OS 1.0.x** (tested on a **SMHUB Nano MG24**, SMHUB OS 1.0.2, AIC8800 Wi-Fi).
- `hostapd` built for the hub (see below): the `wpa_supplicant` shipped with SMHUB OS has no AP mode and there is no `hostapd` package, so NetworkManager cannot create access points.
- Everything else is already in SMHUB OS: `iw`, `dnsmasq`, `iptables`, `nmcli`, `qrencode` and Python 3 (standard library only).

**2.4 GHz only.** On the SMHUB Nano, starting an access point on 5 GHz hangs the AIC8800 driver and the hardware watchdog reboots the hub, so the app never uses 5 GHz. The AP also does not transmit while the hub itself is connected to a Wi-Fi on 5 GHz, so by default (option **Keep the hub Wi-Fi on 2.4 GHz**) the app locks the hub's Wi-Fi profiles to 2.4 GHz while the AP is on and restores their original band when it is turned off.

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
- Turning the AP on or off does not touch Ethernet or the hub's own Wi-Fi connection.
