import socket

class Client:
    ip: str
    port: int
    addr: tuple[str, int]
    size: int
    format: str
    server_data_path: str
    client: socket.socket

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 4450,
        size: int = 1024,
        format: str = "utf-8",
        server_data_path: str = "server_data",
    ):
        self.ip = ip
        self.port = port
        self.addr = (ip, port)
        self.size = size
        self.format = format
        self.server_data_path = server_data_path

    def connect(self):
        self.client = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.client.connect(self.addr)

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
        if command == "LOGOUT":
            self.client.send(command.encode(self.format))
            return False
        if command in ("TASK", "HELP"):
            self.client.send(command.encode(self.format))
        return True
        


    def main(self):
        while True:
            response = self.client.recv(self.size).decode(self.format)

            if not self.handle_response(response):
                break

            line = input("> ")
            parts = line.split(" ")
            command = parts[0]

            if not self.send(command):
                break

        print("Disconnected from the server.")
        self.client.close()
            



    