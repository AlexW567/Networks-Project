from dataclasses import dataclass
import socket

@dataclass
class Connection:
    ip: str = "127.0.0.1"
    port: int = 4450
    addr: tuple[str, int] = (ip, port)
    size: int = 1024
    format: str = "utf-8"
    server_data_path: str = "server_data"
    client: socket.socket | None = None


# 25 MB / 512 KB / 10 B, the way the help doc's sample output shows sizes
def fmt_size(n: int) -> str:
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.0f} {unit}" if n >= 10 * div else f"{n / div:.1f} {unit}"
    return f"{n} B"
