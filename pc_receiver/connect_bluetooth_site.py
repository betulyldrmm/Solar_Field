#!/usr/bin/env python3
"""Connect PC to Arduino UNO Q (`SolarFieldStation`) over Bluetooth PAN and open the site.

Also supports `--watch` daemon mode: listens for Bluetooth connection to UNO Q
(`SolarFieldStation` / `14:B5:CD:EB:31:D7`), brings up the Bluetooth PAN `bnep`
network link automatically, and opens `http://10.44.0.1/` in your default browser!

Usage:
    python3 pc_receiver/connect_bluetooth_site.py           # connect now & open site
    python3 pc_receiver/connect_bluetooth_site.py --watch   # auto-open site whenever BT connects
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
import webbrowser

TARGET_NAME = "SolarFieldStation"
DEFAULT_MAC = "14:B5:CD:EB:31:D7"
SITE_URL = "http://10.44.0.1/"


def _run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def find_uno_q_mac(explicit_mac: str | None = None) -> str:
    if explicit_mac:
        return explicit_mac.upper()
    devs = _run(["bluetoothctl", "devices"]).stdout
    for line in devs.splitlines():
        if TARGET_NAME in line or "uno-q" in line.lower() or DEFAULT_MAC in line.upper():
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].upper()
    print(f"Scanning Bluetooth for {TARGET_NAME} ({DEFAULT_MAC})...", flush=True)
    _run(["bluetoothctl", "--timeout", "6", "scan", "on"], timeout=10.0)
    devs = _run(["bluetoothctl", "devices"]).stdout
    for line in devs.splitlines():
        if TARGET_NAME in line or "uno-q" in line.lower() or DEFAULT_MAC in line.upper():
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].upper()
    return DEFAULT_MAC


def connect_pan_and_open(mac: str) -> bool:
    dbus_path = "/org/bluez/hci0/dev_" + mac.upper().replace(":", "_")
    _run(["bluetoothctl", "trust", mac])
    _run(["bluetoothctl", "pair", mac])

    # Ask NetworkManager (if present) or BlueZ Network1 D-Bus directly to connect PAN ("nap")
    nm = _run(["nmcli", "device", "connect", mac])
    if nm.returncode != 0:
        bus = _run([
            "busctl", "call", "org.bluez", dbus_path,
            "org.bluez.Network1", "Connect", "s", "nap",
        ])
        if bus.returncode == 0:
            # e.g. s "bnep0"
            iface = bus.stdout.strip().split()[-1].strip('"') if bus.stdout.strip() else "bnep0"
            _run(["nmcli", "device", "connect", iface])

    for _ in range(20):
        try:
            with urllib.request.urlopen(f"{SITE_URL}api/health", timeout=1.5) as resp:
                if resp.status == 200:
                    print(f"Connected! Opening {SITE_URL} ...", flush=True)
                    webbrowser.open(SITE_URL)
                    return True
        except Exception:
            time.sleep(0.6)
    print(f"Bluetooth connected; open {SITE_URL} once DHCP finishes.", flush=True)
    webbrowser.open(SITE_URL)
    return False


def watch_loop(mac: str) -> None:
    print(f"Watching Bluetooth connection to {TARGET_NAME} ({mac})...", flush=True)
    was_connected = False
    while True:
        info = _run(["bluetoothctl", "info", mac]).stdout
        is_connected = "Connected: yes" in info
        if is_connected and not was_connected:
            print(f"Detected Bluetooth connection to {mac} -> opening Solar Field Station!", flush=True)
            connect_pan_and_open(mac)
        was_connected = is_connected
        time.sleep(2.5)


def main() -> int:
    p = argparse.ArgumentParser(description="Connect to Arduino UNO Q via Bluetooth and open Solar Field site")
    p.add_argument("--mac", help=f"UNO Q Bluetooth MAC (default: {DEFAULT_MAC})")
    p.add_argument("--watch", action="store_true", help="Watch Bluetooth and auto-open site on connect")
    args = p.parse_args()
    mac = find_uno_q_mac(args.mac)
    if args.watch:
        watch_loop(mac)
    else:
        connect_pan_and_open(mac)
    return 0


if __name__ == "__main__":
    sys.exit(main())
