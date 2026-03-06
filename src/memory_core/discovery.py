"""mDNS service registration and discovery for ClickMem on LAN.

Uses zeroconf to advertise and find ClickMem servers as _clickmem._tcp.local.
"""

from __future__ import annotations

import socket
from typing import Callable


SERVICE_TYPE = "_clickmem._tcp.local."
SERVICE_NAME = "ClickMem Memory Server._clickmem._tcp.local."


def get_local_ip() -> str:
    """Best-effort detection of the machine's LAN IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def register_service(ip: str, port: int) -> Callable[[], None]:
    """Register a ClickMem server on mDNS. Returns a cleanup function."""
    from zeroconf import ServiceInfo, Zeroconf

    info = ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={
            "version": "0.1.0",
            "api": "rest+mcp",
        },
    )

    zc = Zeroconf()
    zc.register_service(info)

    def cleanup():
        zc.unregister_service(info)
        zc.close()

    return cleanup


def discover(timeout: float = 3.0) -> list[dict]:
    """Discover ClickMem servers on the local network.

    Returns a list of {"host": ..., "port": ..., "properties": ...} dicts.
    """
    from zeroconf import ServiceBrowser, Zeroconf
    import time

    found: list[dict] = []

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name)
            if info:
                for addr in info.parsed_addresses():
                    found.append({
                        "host": addr,
                        "port": info.port,
                        "name": info.name,
                        "properties": {
                            k.decode() if isinstance(k, bytes) else k:
                            v.decode() if isinstance(v, bytes) else v
                            for k, v in info.properties.items()
                        },
                    })

        def remove_service(self, zc, type_, name):
            pass

        def update_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    listener = Listener()
    ServiceBrowser(zc, SERVICE_TYPE, listener)
    time.sleep(timeout)
    zc.close()

    return found


def discover_one(timeout: float = 3.0) -> str | None:
    """Find the first ClickMem server on LAN. Returns URL or None."""
    servers = discover(timeout=timeout)
    if servers:
        s = servers[0]
        return f"http://{s['host']}:{s['port']}"
    return None
