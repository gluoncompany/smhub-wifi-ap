# Changelog

## 1.0.8
- The "Exclusive AP" option is gone: the hub Wi-Fi is always disconnected while the AP is on (AP + client at the same time did not work reliably on the AIC8800).
- Removed what only existed for AP + client mode: the "Keep the hub Wi-Fi on 2.4 GHz" option (band lock), following the hub Wi-Fi channel (and HT40), and the "hub on 5 GHz" / "different channel" warnings. The automatic channel is always the least busy of 1, 6 and 11.
- On upgrade, a band lock left by older versions is undone at startup; old `exclusive` / `lock_24` settings are ignored.

## 1.0.7
- 5 GHz option removed again after testing it in exclusive mode: hostapd reports AP-ENABLED on channels 36-48 but the network is not visible. The app is 2.4 GHz only.
- Startup is faster in exclusive mode: no longer waits up to 45 s for the hub Wi-Fi at boot.

## 1.0.6
- Confirmation before turning the AP on in exclusive mode: warns that the hub Wi-Fi will be disconnected, shows whether a wired connection is detected (and blocks activation without cable when internet sharing is on) and warns if the panel is being used through the hub Wi-Fi.
- Warning banner when exclusive mode is on and no wired connection is detected.
- Experimental 5 GHz option (removed in 1.0.7).

## 1.0.5
- New "Exclusive AP" mode (on by default): while the AP is on, the hub Wi-Fi client is disconnected (its profiles lose autoconnect) and restored when the AP is turned off or the service stops. The hub uses Ethernet. With the AIC8800, the AP and a connected Wi-Fi client do not work reliably together (beacons are intermittent).
- Exclusive mode needs a wired uplink when internet sharing is on (`err_no_ethernet`).
- Fixed the watchdog overwriting the "reconnect on 2.4 GHz" decision.

## 1.0.4
- When following the hub Wi-Fi channel, the AP uses the same channel width (HT40+/-) as the client.

## 1.0.3
- New option "Keep the hub Wi-Fi on 2.4 GHz" (on by default): while the AP is on, the hub's Wi-Fi profiles are locked to 2.4 GHz and the hub reconnects if it was on 5 GHz; the original band is restored when the AP is turned off. With the AIC8800 the AP does not transmit while the hub is connected on 5 GHz (hostapd reports AP-ENABLED but no beacons are sent).

## 1.0.2
- Automatic channel: when the hub is not connected to a 2.4 GHz Wi-Fi (Ethernet only, or 5 GHz uplink) the AP scans and uses the least busy of channels 1, 6 and 11. If the hub later joins a 2.4 GHz network, the AP moves to its channel.

## 1.0.1
- 2.4 GHz only: starting an AP on 5 GHz hangs the AIC8800 driver and the hardware watchdog reboots the hub.

## 1.0.0
- First release: AP + client at the same time, hostapd (WPA2/WPA3), DHCP/DNS with dnsmasq, NAT, QR code, connected devices, ES/EN UI.
