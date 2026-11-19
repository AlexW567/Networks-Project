import os
import socket
import threading
import hashlib
import hmac
import re
import csv
import time
from datetime import datetime

from connection import Connection, fmt_size

# File naming system: uploads are stored as TS001.txt / AS001.wav / VS001.mp4 (Text, Audio, Video + running number)
AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
VIDEO = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
KIND_NAMES = {"TS": "Text", "AS": "Audio", "VS": "Video"}
STORED_NAME = re.compile(r"^(TS|AS|VS)\d{3,}\.")  # a name that already follows the system

# shortcut: anything that isn't audio/video counts as Text, add more extension lists if the demo needs them
def prefix_for(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return "AS" if ext in AUDIO else "VS" if ext in VIDEO else "TS"


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
        # network analysis: every finished upload/download is a row in stats.csv (next to server_data, so clients can't reach it)
        self.stats_path = os.path.join(os.path.dirname(os.path.abspath(server_data_path)), "stats.csv")
        self.stats_lock = threading.Lock()  # one thread writes the CSV at a time
        self.clients: dict[tuple, str] = {}  # connected clients: address -> username (or "not logged in")
        self.clients_lock = threading.Lock()  # every client thread edits self.clients
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
        self.update_clients(addr, "not logged in")

        try:
            while True:
                data = conn.recv(self.connection.size).decode(self.connection.format)
                if not data or data == "LOGOUT":  # empty data = client closed the connection
                    break
                new_session = self.handle_command(conn, data, session)
                if new_session != session:  # a login or failed login changed who this client is
                    self.update_clients(addr, new_session or "not logged in")
                session = new_session
        except (ConnectionResetError, ConnectionAbortedError):  # client closed its window without LOGOUT
            pass
        finally:
            self.update_clients(addr, None)  # always drop the client, whatever ended the thread

        print(f"{addr} disconnected")
        conn.close()

    # Add/update a client in the connected-clients list (user=None removes it), then print who is online
    def update_clients(self, addr, user: str | None):
        with self.clients_lock:
            if user is None:
                self.clients.pop(addr, None)
            else:
                self.clients[addr] = user
            online = [f"{a[0]}:{a[1]} ({u})" for a, u in self.clients.items()]
        print(f"[CLIENTS ONLINE: {len(online)}] " + ", ".join(online))

    def handle_command(self, conn, data: str, session: str | None):
        code, *args = data.split("@")  # messages look like "COMMAND@arg1@arg2"
        match code:
            case "HELP":
                self.handle_help(conn)
            case "LOGIN":
                return self.handle_login(conn, args)
            case "PROFILE":
                self.handle_profile(conn, session)
            case "DIR" | "SUBFOLDER" | "UPLOAD" | "DOWNLOAD" | "DELETE" if session is None:  # file commands need a login
                conn.send("OK@Please LOGIN first.".encode(self.connection.format))
            case "DIR":
                self.handle_dir(conn, args)
            case "SUBFOLDER":
                self.handle_subfolder(conn, args)
            case "UPLOAD":
                self.handle_upload(conn, args, session)
            case "DOWNLOAD":
                self.handle_download(conn, args, session)
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
            "DOWNLOAD <server file> [local folder] - Download a file from the server.\n" \
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
        rows = ["Server directory:", "", f"{'Filename':<20}{'Size':<12}Type", "-" * 44]
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if name.endswith(".part"):  # half-finished upload, not a real file yet
                continue
            if os.path.isdir(full):
                rows.append(f"{name + '/':<20}{'-':<12}Folder")
            else:
                rows.append(f"{name:<20}{fmt_size(os.path.getsize(full)):<12}{KIND_NAMES[prefix_for(name)]}")
        conn.send(("OK@" + "\n".join(rows)).encode(self.connection.format))

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
    def handle_upload(self, conn, args: list[str], user: str):
        if len(args) != 2 or not args[0].isdigit():
            conn.send("OK@Usage: UPLOAD <local file> [server folder]".encode(self.connection.format))
            return
        size, dest = int(args[0]), self.safe_path(args[1])
        if dest is None or not os.path.isdir(os.path.dirname(dest)):
            conn.send(
                "OK@Error: server folder not found. "
                "Folders are relative to server storage (not a folder named server_data). "
                "Create one with SUBFOLDER create <path>, or omit the folder to upload to the root."
                .encode(self.connection.format)
            )
            return
        if STORED_NAME.match(os.path.basename(dest)):  # already TS001-style: the client chose this file, so overwrite rules apply
            claimed = self.claim(dest)
        else:  # any other name gets the next free TS/AS/VS number
            dest, claimed = self.new_stored_path(dest), True
        if not claimed:
            conn.send("OK@Error: that file is currently being processed.".encode(self.connection.format))
            return
        try:
            self.receive_file(conn, os.path.relpath(dest, os.path.realpath(self.connection.server_data_path)).replace(os.sep, "/"), size, dest, user)
        finally:
            self.release(dest)  # always free the file, even if the client drops mid-upload

    # The part of UPLOAD that runs while the file is claimed
    def receive_file(self, conn, name: str, size: int, dest: str, user: str):
        if os.path.exists(dest):  # ask the client before overwriting (assignment requires it)
            conn.send("OK@EXISTS".encode(self.connection.format))
            if conn.recv(self.connection.size).decode(self.connection.format).upper() != "Y":
                conn.send("OK@Upload cancelled, file not overwritten.".encode(self.connection.format))
                return
        conn.send("OK@READY".encode(self.connection.format))
        start = time.time()  # transfer time = READY sent until the last byte arrived
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
        seconds = time.time() - start
        os.replace(part, dest)
        self.record_stats("UPLOAD", conn, user, name, size, seconds)
        conn.send(f"OK@Uploaded as {name} ({fmt_size(size)})".encode(self.connection.format))

    # DOWNLOAD@<server path>: reply READY@<size>, then send exactly <size> raw bytes in chunks
    def handle_download(self, conn, args: list[str], user: str):
        if len(args) != 1:
            conn.send("OK@Usage: DOWNLOAD <server file> [local folder]".encode(self.connection.format))
            return
        path = self.safe_path(args[0])
        if path is None or not os.path.isfile(path):
            conn.send(f"OK@Error: {args[0]} does not exist.".encode(self.connection.format))
            return
        if not self.claim(path):
            conn.send("OK@Error: that file is currently being processed.".encode(self.connection.format))
            return
        try:
            self.send_file(conn, args[0], path, user)
        finally:
            self.release(path)

    def send_file(self, conn, name: str, src: str, user: str):
        size = os.path.getsize(src)
        conn.send(f"OK@READY@{size}\n".encode(self.connection.format))  # newline marks where the header ends and file bytes begin
        start = time.time()
        with open(src, "rb") as f:
            while chunk := f.read(65536):
                conn.sendall(chunk)
        self.record_stats("DOWNLOAD", conn, user, name, size, time.time() - start)
        conn.send(f"OK@Downloaded {name} ({fmt_size(size)})\n".encode(self.connection.format))  # newline so the client knows the reply is complete

    # Add one row to stats.csv (header written the first time) and print an [ANALYSIS] line like the help doc
    def record_stats(self, operation: str, conn, user: str, name: str, size: int, seconds: float):
        seconds = max(seconds, 0.001)  # an instant transfer would divide by zero
        rate = size / seconds / (1 << 20)
        with self.stats_lock:
            is_new = not os.path.exists(self.stats_path)
            with open(self.stats_path, "a", newline="") as f:
                writer = csv.writer(f)
                if is_new:
                    writer.writerow(["time", "client_ip", "user", "operation", "file", "size_bytes", "seconds", "mb_per_s"])
                writer.writerow([datetime.now().isoformat(timespec="seconds"), conn.getpeername()[0], user, operation,
                                 name, size, f"{seconds:.3f}", f"{rate:.2f}"])
        print(f"[ANALYSIS] {operation.title()}: {fmt_size(size)} / {seconds:.2f} s, rate {rate:.2f} MB/s")

    # Mark a file as in use. Returns False if another client is already using it.
    # UPLOAD, DOWNLOAD and DELETE all call this so they can't touch the same file at once.
    def claim(self, path: str) -> bool:
        with self.busy_lock:
            if path in self.busy:
                return False
            self.busy.add(path)
            return True

    # Pick the next free name like TS003.txt in dest's folder and claim it in one step, so two uploads can't get the same number.
    # The number counts across ALL folders, so a stored name is unique on the whole server.
    def new_stored_path(self, dest: str) -> str:
        prefix, ext = prefix_for(dest), os.path.splitext(dest)[1].lower()
        pattern = re.compile(rf"^{prefix}(\d+)\.")
        with self.busy_lock:
            names = [f for _, _, files in os.walk(self.connection.server_data_path) for f in files]
            names += [os.path.basename(p) for p in self.busy]  # numbers other uploads are using right now
            highest = max((int(m.group(1)) for f in names if (m := pattern.match(f))), default=0)
            path = os.path.join(os.path.dirname(dest), f"{prefix}{highest + 1:03d}{ext}")
            self.busy.add(path)
            return path

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

    # LOGIN@<user>: challenge-response so the password never crosses the network.
    # Server sends a random nonce; client answers sha256(nonce + sha256(password)); we compute the same from our stored hash.
    def handle_login(self, conn, args: list[str]) -> str | None:
        username = args[0] if args else ""
        nonce = os.urandom(16).hex()  # new every login, so a captured answer can't be replayed
        conn.send(f"OK@CHALLENGE@{nonce}".encode(self.connection.format))
        answer = conn.recv(self.connection.size).decode(self.connection.format)

        if username not in self.users:
            conn.send("OK@User not found".encode(self.connection.format))
            return
        expected = hashlib.sha256((nonce + self.passwords[self.users[username]]).encode()).hexdigest()
        if not hmac.compare_digest(answer, expected):
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

