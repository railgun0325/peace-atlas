from __future__ import annotations

import argparse
import select
import socket
import threading


def relay_pair(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    try:
        while sockets:
            readable, _, _ = select.select(sockets, [], [], 120)
            if not readable:
                break
            for src in readable:
                dst = right if src is left else left
                try:
                    data = src.recv(65536)
                except OSError:
                    data = b""
                if data:
                    dst.sendall(data)
                    continue
                if src in sockets:
                    sockets.remove(src)
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
    except OSError:
        pass
    finally:
        for sock in (left, right):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def handle(client: socket.socket, target_host: str, target_port: int) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=10)
    except OSError:
        client.close()
        return
    relay_pair(client, upstream)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=17897)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=7897)
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen_host, args.listen_port))
    server.listen(128)
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle, args=(client, args.target_host, args.target_port), daemon=True).start()


if __name__ == "__main__":
    main()
