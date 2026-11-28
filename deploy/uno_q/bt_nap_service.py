#!/usr/bin/env python3
"""Bluetooth Network Access Point (PAN/NAP) + Auto-Pairing Agent for Arduino UNO Q.

Creates a `pan0` bridge (`10.44.0.1/24`), configures BlueZ `/org/bluez/hci0` as
`SolarFieldStation` (always discoverable & pairable), registers a Just-Works
pairing agent (`NoInputNoOutput` + fallback PIN `vegas123`), registers BlueZ
`NetworkServer1("nap", "pan0")`, and runs `dnsmasq` with DHCP Option 114
(RFC 8910 Captive Portal) + wildcard DNS so connecting via Bluetooth immediately
opens the Solar Field Station web dashboard.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

log = logging.getLogger("sfs.bt_nap")

BRIDGE_IFACE = "pan0"
BRIDGE_IP = "10.44.0.1"
BRIDGE_CIDR = "10.44.0.1/24"
DEVICE_ALIAS = "SolarFieldStation"
FALLBACK_PIN = "vegas123"
AGENT_PATH = "/org/solarfield/agent"
DNSMASQ_PID = Path("/run/sfs-dnsmasq-pan0.pid")


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def setup_bridge_and_dnsmasq() -> subprocess.Popen[bytes] | None:
    """Create `pan0` bridge, allow bridge/NAT forwarding, and start dnsmasq."""
    if _run(["ip", "link", "show", BRIDGE_IFACE]).returncode != 0:
        _run(["ip", "link", "add", "name", BRIDGE_IFACE, "type", "bridge"], check=True)
    _run(["ip", "link", "set", BRIDGE_IFACE, "type", "bridge", "stp_state", "0", "forward_delay", "0"])
    _run(["nmcli", "device", "set", BRIDGE_IFACE, "managed", "no"])
    addrs = _run(["ip", "-4", "addr", "show", "dev", BRIDGE_IFACE]).stdout
    if BRIDGE_IP not in addrs:
        _run(["ip", "addr", "add", BRIDGE_CIDR, "dev", BRIDGE_IFACE], check=True)
    _run(["ip", "link", "set", BRIDGE_IFACE, "up"], check=True)

    # Prevent Docker's br_netfilter FORWARD DROP policy from dropping Bluetooth BNEP packets
    _run(["sysctl", "-w", "net.bridge.bridge-nf-call-iptables=0"])
    _run(["sysctl", "-w", "net.bridge.bridge-nf-call-ip6tables=0"])
    _run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    _run(["sysctl", "-w", "net.ipv4.conf.all.rp_filter=0"])
    _run(["sysctl", "-w", f"net.ipv4.conf.{BRIDGE_IFACE}.rp_filter=0"])
    if _run(["iptables", "-C", "FORWARD", "-i", BRIDGE_IFACE, "-j", "ACCEPT"]).returncode != 0:
        _run(["iptables", "-I", "FORWARD", "1", "-i", BRIDGE_IFACE, "-j", "ACCEPT"])
    if _run(["iptables", "-C", "FORWARD", "-o", BRIDGE_IFACE, "-j", "ACCEPT"]).returncode != 0:
        _run(["iptables", "-I", "FORWARD", "1", "-o", BRIDGE_IFACE, "-j", "ACCEPT"])
    if _run([
        "iptables", "-t", "nat", "-C", "POSTROUTING",
        "-s", "10.44.0.0/24", "!", "-d", "10.44.0.0/24", "-j", "MASQUERADE",
    ]).returncode != 0:
        _run([
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-s", "10.44.0.0/24", "!", "-d", "10.44.0.0/24", "-j", "MASQUERADE",
        ])

    if DNSMASQ_PID.exists():
        try:
            old_pid = int(DNSMASQ_PID.read_text().strip())
            _run(["kill", str(old_pid)])
        except (OSError, ValueError):
            pass
        DNSMASQ_PID.unlink(missing_ok=True)

    cmd = [
        "/usr/sbin/dnsmasq",
        "--keep-in-foreground",
        f"--interface={BRIDGE_IFACE}",
        "--bind-interfaces",
        "--except-interface=lo",
        f"--listen-address={BRIDGE_IP}",
        "--dhcp-range=10.44.0.10,10.44.0.60,255.255.255.0,12h",
        f"--dhcp-option=3,{BRIDGE_IP}",
        f"--dhcp-option=6,{BRIDGE_IP}",
        f"--address=/solar.field/{BRIDGE_IP}",
        f"--address=/uno-q.local/{BRIDGE_IP}",
        f"--address=/connectivitycheck.gstatic.com/{BRIDGE_IP}",
        f"--address=/www.gstatic.com/{BRIDGE_IP}",
        f"--address=/clients3.google.com/{BRIDGE_IP}",
        f"--address=/captive.apple.com/{BRIDGE_IP}",
        f"--address=/www.msftconnecttest.com/{BRIDGE_IP}",
        "--server=8.8.8.8",
        "--no-resolv",
        "--no-hosts",
        "--dhcp-authoritative",
        f"--pid-file={DNSMASQ_PID}",
    ]
    proc = subprocess.Popen(cmd)
    log.info("started dnsmasq on %s (%s) pid=%s", BRIDGE_IFACE, BRIDGE_CIDR, proc.pid)
    return proc


class AutoPairAgent(dbus.service.Object):
    """Headless BlueZ Agent1: accepts pairing and NAP/GATT service connections."""

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self) -> None:
        log.info("BlueZ agent released")

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device: str, uuid: str) -> None:
        log.info("authorizing service %s for %s", uuid, device)
        self._trust(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device: str) -> str:
        log.info("PIN requested by %s -> returning fallback PIN", device)
        self._trust(device)
        return FALLBACK_PIN

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device: str) -> dbus.UInt32:
        self._trust(device)
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device: str, passkey: int, entered: int) -> None:
        log.info("passkey %06d for %s", passkey, device)

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device: str, pincode: str) -> None:
        log.info("pincode %s for %s", pincode, device)

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device: str, passkey: int) -> None:
        log.info("auto-confirming SSP passkey %06d for %s", passkey, device)
        self._trust(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device: str) -> None:
        log.info("auto-authorizing %s", device)
        self._trust(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self) -> None:
        pass

    @staticmethod
    def _trust(device_path: str) -> None:
        try:
            bus = dbus.SystemBus()
            props = dbus.Interface(bus.get_object("org.bluez", device_path), "org.freedesktop.DBus.Properties")
            props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
        except Exception as exc:
            log.debug("could not mark %s trusted yet: %s", device_path, exc)


def configure_adapter_and_nap(bus: dbus.SystemBus) -> dbus.Interface:
    adapter_obj = bus.get_object("org.bluez", "/org/bluez/hci0")
    props = dbus.Interface(adapter_obj, "org.freedesktop.DBus.Properties")
    props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
    props.Set("org.bluez.Adapter1", "Alias", dbus.String(DEVICE_ALIAS))
    props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
    props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))
    props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
    props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))

    net_server = dbus.Interface(adapter_obj, "org.bluez.NetworkServer1")
    try:
        net_server.Unregister("nap")
    except dbus.exceptions.DBusException:
        pass
    net_server.Register("nap", BRIDGE_IFACE)
    log.info("registered BlueZ NetworkServer1('nap', '%s') as '%s'", BRIDGE_IFACE, DEVICE_ALIAS)
    return net_server


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dns_proc = setup_bridge_and_dnsmasq()
    bus = dbus.SystemBus()

    agent = AutoPairAgent(bus, AGENT_PATH)
    mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    mgr.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    mgr.RequestDefaultAgent(AGENT_PATH)
    log.info("registered default NoInputNoOutput pairing agent at %s", AGENT_PATH)

    net_server = configure_adapter_and_nap(bus)
    loop = GLib.MainLoop()

    def _keep_discoverable() -> bool:
        try:
            adapter_obj = bus.get_object("org.bluez", "/org/bluez/hci0")
            props = dbus.Interface(adapter_obj, "org.freedesktop.DBus.Properties")
            if not bool(props.Get("org.bluez.Adapter1", "Discoverable")):
                props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
            if _run(["ip", "link", "show", BRIDGE_IFACE]).returncode == 0:
                _run(["ip", "link", "set", BRIDGE_IFACE, "up"])
        except Exception as exc:
            log.warning("periodic bluetooth check: %s", exc)
        return True

    GLib.timeout_add_seconds(20, _keep_discoverable)

    def _shutdown(*_args) -> None:
        loop.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run()
    finally:
        try:
            net_server.Unregister("nap")
        except Exception:
            pass
        try:
            mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        if dns_proc is not None:
            dns_proc.terminate()
            dns_proc.wait(timeout=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
