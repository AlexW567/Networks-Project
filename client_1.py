from client import Client

# Use 127.0.0.1 when the server runs on the same machine.
IP = "127.0.0.1"
PORT = 12345


def main():
    client = Client(ip=IP, port=PORT)
    client.connect()
    client.main()


if __name__ == "__main__":
    main()
