from client import Client

# School VPN server; use 127.0.0.1 when the server runs on the same machine.
IP = "10.200.102.237"
PORT = 12345


def main():
    client = Client(ip=IP, port=PORT)
    client.connect()
    client.main()


if __name__ == "__main__":
    main()
