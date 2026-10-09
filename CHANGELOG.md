# Changelog

## 1.0.3
- New option "Keep the hub Wi-Fi on 2.4 GHz" (on by default): while the AP is on, the hub's Wi-Fi profiles are locked to 2.4 GHz and the hub reconnects if it was on 5 GHz; the original band is restored when the AP is turned off. With the AIC8800 the AP does not transmit while the hub is connected on 5 GHz (hostapd reports AP-ENABLED but no beacons are sent).

## 1.0.2
- Automatic channel: when the hub is not connected to a 2.4 GHz Wi-Fi (Ethernet only, or 5 GHz uplink) the AP scans and uses the least busy of channels 1, 6 and 11. If the hub later joins a 2.4 GHz network, the AP moves to its channel.

## 1.0.1
- 2.4 GHz only: starting an AP on 5 GHz hangs the AIC8800 driver and the hardware watchdog reboots the hub.

## 1.0.0
- First release: AP + client at the same time, hostapd (WPA2/WPA3), DHCP/DNS with dnsmasq, NAT, QR code, connected devices, ES/EN UI.
