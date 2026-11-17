import os
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
        self.busy: set[str] = set()  # files being uploaded/downloaded/deleted right now
        self.busy_lock = threading.Lock()  # threads share self.busy, so guard it
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
        os.makedirs(self.connection.server_data_path, exist_ok=True)  # folder where all client files live
        print(f"server is listening on {self.connection.ip}: {self.connection.port}")

    def run_client(self, conn, addr):
        print(f"[NEW CONNECTION] {addr} connected.")
        conn.send("OK@Welcome to the server".encode(self.connection.format))

        session = None

        try:
            while True:
                data = conn.recv(self.connection.size).decode(self.connection.format)
                if not data or data == "LOGOUT":  # empty data = client closed the connection
                    break
                session = self.handle_command(conn, data, session)
        except (ConnectionResetError, ConnectionAbortedError):  # client closed its window without LOGOUT
            pass

        print(f"{addr} disconnected")
        conn.close()

    def handle_command(self, conn, data: str, session: str | None):
        code, *args = data.split("@")  # messages look like "COMMAND@arg1@arg2"
        match code:
            case "HELP":
                self.handle_help(conn)
            case "LOGIN":
                return self.handle_login(conn)
            case "PROFILE":
                self.handle_profile(conn, session)
            case "DIR" | "SUBFOLDER" | "UPLOAD" | "DELETE" if session is None:  # file commands need a login
                conn.send("OK@Please LOGIN first.".encode(self.connection.format))
            case "DIR":
                self.handle_dir(conn, args)
            case "SUBFOLDER":
                self.handle_subfolder(conn, args)
            case "UPLOAD":
                self.handle_upload(conn, args)
            case "DELETE":
                self.handle_delete(conn, args)
            case _:
                conn.send(f"OK@Unknown command: {code!r}\n".encode(self.connection.format))
        return session  # keep the login for every non-LOGIN command (returning None logged the user out)

    def handle_help(self, conn):
        conn.send("OK@HELP - Available commands:\n" \
            "LOGIN - Login to the server.\n" \
            "PROFILE - Get the profile of the user.\n" \
            "DIR [folder] - List files and folders on the server.\n" \
            "SUBFOLDER create|delete <path> - Make or remove a folder on the server.\n" \
            "UPLOAD <local file> [folder] - Upload a file to the server.\n" \
            "DELETE <file> - Delete a file on the server.\n" \
            "LOGOUT - Disconnect from the server.\n".encode(self.connection.format))

    # Turn a client-given path into a real path inside server_data.
    # Returns None if it tries to escape (e.g. "../../Desktop"), so clients can't touch other folders.
    def safe_path(self, path: str) -> str | None:
        root = os.path.realpath(self.connection.server_data_path)
        full = os.path.realpath(os.path.join(root, path))
        return full if os.path.commonpath([root, full]) == root else None

    # DIR [folder]: list what's in server_data (or a subfolder of it). Folders end with "/".
    def handle_dir(self, conn, args: list[str]):
        folder = self.safe_path(args[0] if args else "")
        if folder is None or not os.path.isdir(folder):
            conn.send("OK@Error: folder not found.".encode(self.connection.format))
            return
        entries = sorted(os.listdir(folder))
        lines = [name + "/" if os.path.isdir(os.path.join(folder, name)) else name for name in entries]
        conn.send(("OK@" + ("\n".join(lines) if lines else "(empty)")).encode(self.connection.format))

    # SUBFOLDER create|delete <path>: make or remove a folder inside server_data
    def handle_subfolder(self, conn, args: list[str]):
        if len(args) != 2 or args[0].lower() not in ("create", "delete"):
            conn.send("OK@Usage: SUBFOLDER create|delete <path>".encode(self.connection.format))
            return
        action, path = args[0].lower(), args[1]
        folder = self.safe_path(path)
        if folder is None or folder == os.path.realpath(self.connection.server_data_path):
            conn.send("OK@Error: that path is not allowed.".encode(self.connection.format))
            return
        try:
            if action == "create":
                os.makedirs(folder)  # also makes parent folders, e.g. "music/rock"
                conn.send(f"OK@Created folder {path}".encode(self.connection.format))
            else:
                os.rmdir(folder)  # only removes empty folders, so files can't be wiped by accident
                conn.send(f"OK@Deleted folder {path}".encode(self.connection.format))
        except FileExistsError:
            conn.send(f"OK@Error: {path} already exists.".encode(self.connection.format))
        except FileNotFoundError:
            conn.send(f"OK@Error: {path} does not exist.".encode(self.connection.format))
        except OSError:  # folder isn't empty, or it's actually a file
            conn.send(f"OK@Error: {path} is not an empty folder.".encode(self.connection.format))

    # UPLOAD@<size>@<server path>: reply READY, then read exactly <size> raw bytes in chunks
    def handle_upload(self, conn, args: list[str]):
        if len(args) != 2 or not args[0].isdigit():
            conn.send("OK@Usage: UPLOAD <local file> [server folder]".encode(self.connection.format))
            return
        size, dest = int(args[0]), self.safe_path(args[1])
        if dest is None or not os.path.isdir(os.path.dirname(dest)):
            conn.send("OK@Error: server folder not found.".encode(self.connection.format))
            return
        if not self.claim(dest):
            conn.send("OK@Error: that file is currently being processed.".encode(self.connection.format))
            return
        try:
            self.receive_file(conn, args[1], size, dest)
        finally:
            self.release(dest)  # always free the file, even if the client drops mid-upload

    # The part of UPLOAD that runs while the file is claimed
    def receive_file(self, conn, name: str, size: int, dest: str):
        if os.path.exists(dest):  # ask the client before overwriting (assignment requires it)
            conn.send("OK@EXISTS".encode(self.connection.format))
            if conn.recv(self.connection.size).decode(self.connection.format).upper() != "Y":
                conn.send("OK@Upload cancelled, file not overwritten.".encode(self.connection.format))
                return
        conn.send("OK@READY".encode(self.connection.format))
        part, received = dest + ".part", 0  # write to .part so a dropped upload never leaves a half file
        with open(part, "wb") as f:
            while received < size:
                chunk = conn.recv(min(65536, size - received))
                if not chunk:  # client vanished mid-upload
                    break
                f.write(chunk)
                received += len(chunk)
        if received < size:
            os.remove(part)
            raise ConnectionResetError  # run_client already handles this as a disconnect
        os.replace(part, dest)
        conn.send(f"OK@Uploaded {name} ({size} bytes)".encode(self.connection.format))

    # Mark a file as in use. Returns False if another client is already using it.
    # UPLOAD, DOWNLOAD and DELETE all call this so they can't touch the same file at once.
    def claim(self, path: str) -> bool:
        with self.busy_lock:
            if path in self.busy:
                return False
            self.busy.add(path)
            return True

    def release(self, path: str):
        with self.busy_lock:
            self.busy.discard(path)

    # DELETE@<path>: remove one file (folders are removed with SUBFOLDER delete)
    def handle_delete(self, conn, args: list[str]):
        if len(args) != 1:
            conn.send("OK@Usage: DELETE <file>".encode(self.connection.format))
            return
        path = self.safe_path(args[0])
        if path is None or not os.path.isfile(path):
            conn.send(f"OK@Error: {args[0]} does not exist.".encode(self.connection.format))
            return
        if not self.claim(path):
            conn.send(f"OK@Error: {args[0]} is currently being processed.".encode(self.connection.format))
            return
        try:
            os.remove(path)
            conn.send(f"OK@Deleted {args[0]}".encode(self.connection.format))
        except OSError:
            conn.send(f"OK@Error: could not delete {args[0]}.".encode(self.connection.format))
        finally:
            self.release(path)

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

