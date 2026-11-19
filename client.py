import getpass
import hashlib
import os
import socket
import time

from connection import Connection, fmt_size

# Menu from the help doc: number -> (command words, prompts for its arguments). Typing the word itself still works.
MENU = {
    "0": (["LOGIN"], []),
    "1": (["UPLOAD"], ["Local file", "Server folder (Enter = root)"]),
    "2": (["DOWNLOAD"], ["Server file", "Local folder (Enter = here)"]),
    "3": (["DELETE"], ["Server file"]),
    "4": (["DIR"], ["Folder (Enter = root)"]),
    "5": (["SUBFOLDER", "create"], ["New folder"]),
    "6": (["SUBFOLDER", "delete"], ["Folder to delete"]),
    "7": (["LOGOUT"], []),
}
MENU_TEXT = """
========== COMMAND MENU ==========
0. Login
1. Upload
2. Download
3. Delete
4. Dir
5. Subfolder Create
6. Subfolder Delete
7. Logout
==================================
"""
# Read a password and echo one * per key. shortcut: asterisks only on Windows (msvcrt), other systems fall back to a silent prompt.
def ask_password(prompt: str) -> str:
    if os.name != "nt":
        return getpass.getpass(prompt)
    import msvcrt
    print(prompt, end="", flush=True)
    chars: list[str] = []
    while (ch := msvcrt.getwch()) not in ("\r", "\n"):
        if ch == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):  # arrow/function keys send a second character; ignore both
            msvcrt.getwch()
        elif ch == "\b":
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
        else:
            chars.append(ch)
            print("*", end="", flush=True)
    print()
    return "".join(chars)


ERROR_STARTS =("Error", "User not found", "Invalid", "Please LOGIN", "Unknown", "Usage")  # server replies that are failures


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
                tag = "[ERROR]" if message.startswith(ERROR_STARTS) else "[OK]"
                print(f"{tag} {message.removeprefix('Error: ').rstrip()}")
            case "DISCONNECTED":
                print(f"[OK] {message}")
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
            self.pending = self.login()
        elif command == "UPLOAD":
            self.upload(args)
        elif command == "DOWNLOAD":
            self.download(args)
        else:
            # Send every other command (even typos) so the server always replies and we never wait forever.
            # Arguments go after the command, separated by @: "SUBFOLDER@create@music"
            self.connection.client.send("@".join([command, *args]).encode(self.connection.format))
        return command != "LOGOUT"
        


    # Ask for username/password and run the challenge-response. Returns the server's final reply.
    def login(self) -> str:
        username = input("Username: ")
        password = ask_password("Password: ")  # shows * instead of the letters
        sock = self.connection.client
        sock.send(f"LOGIN@{username}".encode(self.connection.format))
        nonce = sock.recv(self.connection.size).decode(self.connection.format).split("@")[2]  # "OK@CHALLENGE@<nonce>"
        # prove we know the password without sending it
        proof = hashlib.sha256((nonce + hashlib.sha256(password.encode()).hexdigest()).encode()).hexdigest()
        sock.send(proof.encode(self.connection.format))
        return sock.recv(self.connection.size).decode(self.connection.format)

    # Read one command. A menu number ("1") asks for its arguments; typed commands ("upload a.txt") work as before.
    def read_command(self) -> tuple[str, list[str]]:
        while True:  # empty input would send 0 bytes and the server would never reply
            parts = input("> ").strip().split()  # split() also ignores extra spaces between words
            if parts:
                break
        if parts[0] in MENU:
            words, prompts = MENU[parts[0]]
            parts = words + (parts[1:] or [a for p in prompts if (a := input(f"{p}: ").strip())])
        elif parts[0].upper() == "DISCONNECT":
            parts[0] = "LOGOUT"
        return parts[0].upper(), parts[1:]  # "help" works the same as "HELP"

    # UPLOAD <local file> [server folder]: send "UPLOAD@size@path", wait for READY, then stream the file
    def upload(self, args: list[str]):
        if not args or not os.path.isfile(args[0]):
            self.pending = "OK@Usage: UPLOAD <local file> [server folder] (file must exist)"
            return
        name = os.path.basename(args[0])
        folder = args[1].rstrip("/") if len(args) > 1 else ""
        # Paths are relative to the server's storage root (server_data/), not a subfolder literally named "server_data".
        if folder in ("", ".", "server_data"):
            dest = name
        else:
            dest = f"{folder}/{name}"
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
        total, sent, start = os.path.getsize(args[0]), 0, time.time()
        print(f"Uploading {name}...")
        with open(args[0], "rb") as f:
            while chunk := f.read(65536):
                sock.sendall(chunk)  # server's final "Uploaded" reply is picked up by main()
                sent += len(chunk)
                self.show_progress(sent, total)
        self.show_stats(total, start)

    # Redraw "Progress: NN%" on one line, only when the percent changes
    def show_progress(self, done: int, total: int):
        percent = done * 100 // total
        if percent != getattr(self, "_shown", -1):
            self._shown = percent
            print(f"\rProgress: {percent}%", end="", flush=True)

    # Final lines of an upload/download, matching the help doc
    def show_stats(self, total: int, start: float):
        self._shown = -1
        seconds = max(time.time() - start, 0.001)
        print(f"\rProgress: 100%\nSize: {fmt_size(total)}\nTransfer Time: {seconds:.2f} seconds"
              f"\nAverage Speed: {total / seconds / (1 << 20):.2f} MB/s")

    # DOWNLOAD <server file> [local folder]: send "DOWNLOAD@path", wait for READY@size, then save the stream
    def download(self, args: list[str]):
        if not args:
            self.pending = "OK@Usage: DOWNLOAD <server file> [local folder]"
            return
        server_path = args[0]
        name = os.path.basename(server_path)
        local = os.path.join(args[1], name) if len(args) > 1 else name
        if not os.path.isdir(os.path.dirname(local) or "."):  # would crash on open() otherwise
            self.pending = "OK@Error: local folder not found."
            return
        if os.path.exists(local):
            if input(f"{local} already exists. Overwrite? (y/n): ").strip().lower() != "y":
                self.pending = "OK@Download cancelled, file not overwritten."
                return
        sock = self.connection.client
        sock.send(f"DOWNLOAD@{server_path}".encode(self.connection.format))
        reply = sock.recv(self.connection.size).decode(self.connection.format)
        parts = reply.split("@")
        if len(parts) < 3 or parts[0] != "OK" or parts[1] != "READY" or not parts[2].isdigit():
            self.pending = reply
            return
        size = int(parts[2])
        part, received, start = local + ".part", 0, time.time()
        print(f"Downloading {name}...")
        with open(part, "wb") as f:
            while received < size:
                chunk = sock.recv(min(65536, size - received))
                if not chunk:
                    f.close()  # Windows can't delete an open file
                    os.remove(part)
                    raise ConnectionResetError
                f.write(chunk)
                received += len(chunk)
                self.show_progress(received, size)
        os.replace(part, local)
        self.show_stats(size, start)
        # server's final "Downloaded" reply is picked up by main()

    def main(self):
        print("FILE SHARING CLIENT v1.0")
        try:
            self.connection.client.recv(self.connection.size)  # server's welcome line; we print our own below
            print(f"[OK] Connected to server {self.connection.ip}:{self.connection.port}.")
            print("Type HELP to see the commands, or LOGIN to sign in.\n")
            while True:
                command, args = self.read_command()
                if not self.send(command, args):
                    break
                response = self.pending or self.connection.client.recv(self.connection.size).decode(self.connection.format)
                self.pending = None
                print()
                if not self.handle_response(response):
                    break
                if command == "HELP":  # the numbered menu only shows up when asked for
                    print(MENU_TEXT)
        except (ConnectionResetError, ConnectionAbortedError):  # server crashed or was closed
            print("[ERROR] Lost connection to the server.")
        finally:
            self.connection.client.close()
        print("[OK] Disconnected from server.")


# Run with: python client.py            (server on this PC)
#       or: python client.py SERVER_IP  (server on another PC)
if __name__ == "__main__":
    import sys
    client = Client(ip=sys.argv[1] if len(sys.argv) > 1 else "10.200.102.246")  # school VPN server; pass 127.0.0.1 to test on this PC
    try:
        client.connect()
    except (ConnectionRefusedError, TimeoutError):
        sys.exit(f"Couldn't reach a server at {client.connection.addr}. Is server.py running?")
    client.main()
