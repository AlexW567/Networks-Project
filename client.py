import os
import socket

from connection import Connection


class Client:
    connection: Connection

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 12345,  # matches the server's port
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
        self.pending: str | None = None  # a reply we already have, so main() doesn't wait on the server

    def connect(self):
        self.connection.client = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.connection.client.connect(self.connection.addr)

    """
    Handle the response from the server.
    Returns False if the connection is lost.
    """
    def handle_response(self, data: str) -> bool:
        if not data:  # empty reply = the server closed the connection
            print("Server disconnected.")
            return False
        code, _, message = data.partition("@")  # split on the first @ only, so an @ in the message can't crash it
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
    def send(self, command: str, args: list[str] = []) -> bool:
        if command == "LOGIN":
            self.connection.client.send("LOGIN".encode(self.connection.format))
            username = input("Enter username: ")
            password = input("Enter password: ")
            self.connection.client.send(f"{username}".encode(self.connection.format))
            self.connection.client.send(f"{password}".encode(self.connection.format))
        elif command == "UPLOAD":
            self.upload(args)
        else:
            # Send every other command (even typos) so the server always replies and we never wait forever.
            # Arguments go after the command, separated by @: "SUBFOLDER@create@music"
            self.connection.client.send("@".join([command, *args]).encode(self.connection.format))
        return command != "LOGOUT"
        


    # UPLOAD <local file> [server folder]: send "UPLOAD@size@path", wait for READY, then stream the file
    def upload(self, args: list[str]):
        if not args or not os.path.isfile(args[0]):
            self.pending = "OK@Usage: UPLOAD <local file> [server folder] (file must exist)"
            return
        name = os.path.basename(args[0])
        dest = f"{args[1]}/{name}" if len(args) > 1 else name
        sock = self.connection.client
        sock.send(f"UPLOAD@{os.path.getsize(args[0])}@{dest}".encode(self.connection.format))
        reply = sock.recv(self.connection.size).decode(self.connection.format)
        if reply == "OK@EXISTS":  # file is already on the server: let the user decide
            answer = "Y" if input(f"{dest} already exists. Overwrite? (y/n): ").strip().lower() == "y" else "N"
            sock.send(answer.encode(self.connection.format))
            reply = sock.recv(self.connection.size).decode(self.connection.format)
        if reply != "OK@READY":  # server refused (bad folder) or the user said no
            self.pending = reply
            return
        with open(args[0], "rb") as f:
            while chunk := f.read(65536):
                sock.sendall(chunk)  # server's final "Uploaded" reply is picked up by main()

    def main(self):
        try:
            while True:
                response = self.pending or self.connection.client.recv(self.connection.size).decode(self.connection.format)
                self.pending = None

                if not self.handle_response(response):
                    break

                line = input("> ").strip()
                while not line:  # empty input would send 0 bytes and the server would never reply
                    line = input("> ").strip()
                parts = line.split()  # split() also ignores extra spaces between words
                command = parts[0].upper()  # "help" works the same as "HELP"

                if not self.send(command, parts[1:]):  # parts[1:] = the arguments (folder names, etc.)
                    break
        except (ConnectionResetError, ConnectionAbortedError):  # server crashed or was closed
            print("Lost connection to the server.")

        print("Disconnected from the server.")
        self.connection.client.close()
            



    

# Run with: python client.py            (server on this PC)
#       or: python client.py SERVER_IP  (server on another PC)
if __name__ == "__main__":
    import sys
    client = Client(ip=sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1")
    try:
        client.connect()
    except (ConnectionRefusedError, TimeoutError):
        sys.exit(f"Couldn't reach a server at {client.connection.addr}. Is server.py running?")
    client.main()
