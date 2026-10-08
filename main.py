"""Start the server dashboard with: uv run python main.py."""

import os
import ipaddress
import socket

import uvicorn


def main() -> None:
    host = os.environ.get("MONITOR_HOST", "0.0.0.0")
    raw_port = os.environ.get("MONITOR_PORT", "8859")
    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("MONITOR_PORT must be an integer between 1 and 65535.")

    print("\nSERVER MONITOR | Real-time host metrics", flush=True)
    if host in ("0.0.0.0", "::"):
        print(f"Local:   http://localhost:{port}", flush=True)
        print(f"Network: http://<server-ip>:{port}", flush=True)
        try:
            import psutil

            interface_stats = psutil.net_if_stats()
            addresses = {
                address.address
                for name, interface in psutil.net_if_addrs().items()
                for address in interface
                if name in interface_stats and interface_stats[name].isup
                if address.family == socket.AF_INET
                and not ipaddress.ip_address(address.address).is_loopback
                and not ipaddress.ip_address(address.address).is_link_local
            }
            for address in sorted(addresses):
                print(f"         http://{address}:{port}", flush=True)
        except (OSError, RuntimeError):
            pass
    else:
        url_host = f"[{host}]" if ":" in host else host
        print(f"Open:    http://{url_host}:{port}", flush=True)
    print("Refresh: 1 second | Stop: Ctrl+C\n", flush=True)
    uvicorn.run("server_monitor.app:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
