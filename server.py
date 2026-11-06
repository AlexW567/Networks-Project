import socket
import threading
import hashlib

from connection import Connection


class Server:
    connection: Connection
    users: dict[str, str] = {}
    passwords: dict[str, str] = {}

    def __init__(
        self, 
        ip: str = "0.0.0.0", 
        port: int = 12345,
        size: int = 1024,
        format: str = "utf-8",
        server_data_path: str = "server_data",
    ):
        self.connection = Connection(
            ip=ip,
            port=port,
            addr=(ip, port),
            size=size,
            format=format,
            server_data_path=server_data_path,
            client=None,
        )
        self.users = {
            "user0": "0",
            "user1": "1"
        }
        self.passwords = {
            "0": "1f489582f7ea4c208b70219a2bb6a322227a7516630530a10ed7f2710cfbe447",
            "1": "0b14d501a594442a01c6859541bcb3e8164d183d32937b851835442f69d5c94e"
        }

    def start(self):
        print("Starting the server")
        self.connection.client = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.connection.client.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.connection.client.bind(self.connection.addr)
        self.connection.client.listen()
        print(f"server is listening on {self.connection.ip}: {self.connection.port}")

    def run_client(self, conn, addr):
        print(f"[NEW CONNECTION] {addr} connected.")
        conn.send("OK@Welcome to the server".encode(self.connection.format))

        session = None

        while True:
            data = conn.recv(self.connection.size).decode(self.connection.format)
            session = self.handle_command(conn, data, session)
            ...

        print(f"{addr} disconnected")
        conn.close()

    def handle_command(self, conn, data: str, session: str | None):
        code = data
        match code:
            case "HELP":
                self.handle_help(conn)
            case "LOGIN":
                return self.handle_login(conn)
            case "PROFILE":
                self.handle_profile(conn, session)
            case _:
                conn.send(f"OK@Unknown command: {code!r}\n".encode(self.connection.format))
                ...

    def handle_help(self, conn):
        conn.send("OK@HELP - Available commands:\n" \
            "LOGIN - Login to the server.\n" \
            "PROFILE - Get the profile of the user.\n".encode(self.connection.format))

    def handle_login(self, conn) -> str | None:
        username = conn.recv(self.connection.size).decode(self.connection.format)
        password = conn.recv(self.connection.size).decode(self.connection.format)

        if username not in self.users:
            conn.send("OK@User not found".encode(self.connection.format))
            return
        if self.passwords[self.users[username]] != hashlib.sha256(password.encode()).hexdigest():
            conn.send("OK@Invalid password".encode(self.connection.format))
            return
        
        conn.send(f"OK@Login successful {username}".encode(self.connection.format))
        return username
        
    def handle_profile(self, conn, session: str | None):
        if session is None:
            conn.send("OK@Not logged in".encode(self.connection.format))
            return
        conn.send(f"OK@Profile: {session}".encode(self.connection.format))

    def main(self):
        while True:
            conn, addr = self.connection.client.accept()
            thread = threading.Thread(target = self.run_client, args = (conn, addr))
            thread.start()

        self.connection.client.close()

def main():
    server = Server(ip='0.0.0.0', port=12345)
    server.start()
    server.main()


if __name__ == "__main__":
    main()

