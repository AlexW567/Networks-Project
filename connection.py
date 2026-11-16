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
