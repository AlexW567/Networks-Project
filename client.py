import socket

from connection import Connection


class Client:
    connection: Connection

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 4450,
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

    def connect(self):
        self.connection.client = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.connection.client.connect(self.connection.addr)

    """
    Handle the response from the server.
    Returns False if the connection is lost.
    """
    def handle_response(self, data: str) -> bool:
        code, message = data.split("@")
        match code:
            case "OK":
                print(f"{message}")
            case "DISCONNECTED":
                print(f"{message}")
                return False
            case _:
                print(f"Unknown response: {code}")
            
        return True

    """
    Send a command to the server.
    Returns False if the command is LOGOUT.
    """
    def send(self, command: str) -> bool:
        if command in ("HELP"):
            self.connection.client.send(command.encode(self.connection.format))
        if command == "LOGIN":
            self.connection.client.send("LOGIN".encode(self.connection.format))
            username = input("Enter username: ")
            password = input("Enter password: ")
            self.connection.client.send(f"{username}".encode(self.connection.format))
            self.connection.client.send(f"{password}".encode(self.connection.format))
        if command == "PROFILE":
            self.connection.client.send("PROFILE".encode(self.connection.format))
        return True
        


    def main(self):
        while True:
            response = self.connection.client.recv(self.connection.size).decode(self.connection.format)

            if not self.handle_response(response):
                break

            line = input("> ")
            parts = line.split(" ")
            command = parts[0]

            if not self.send(command):
                break

        print("Disconnected from the server.")
        self.connection.client.close()
            



    