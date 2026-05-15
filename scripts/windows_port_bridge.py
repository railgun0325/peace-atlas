from __future__ import annotations

import argparse
import select
import socket
import threading
import time


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


def serve(listen_host: str, listen_port: int, target_host: str, target_port: int) -> None:
    while True:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((listen_host, listen_port))
            break
        except OSError:
            server.close()
            time.sleep(2)
    server.listen(128)
    while True:
        client, _ = server.accept()
        try:
            upstream = socket.create_connection((target_host, target_port), timeout=10)
        except OSError:
            client.close()
            continue
        threading.Thread(target=relay_pair, args=(client, upstream), daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--ports", nargs="+", type=int, required=True)
    parser.add_argument(
        "--map",
        nargs="*",
        default=[],
        metavar="LOCAL:TARGET",
        help="Additional local-to-target port mappings, for example 5682:5678.",
    )
    args = parser.parse_args()

    for port in args.ports:
        threading.Thread(
            target=serve,
            args=(args.listen_host, port, args.target_host, port),
            daemon=True,
        ).start()
    for item in args.map:
        local_port, target_port = [int(part) for part in item.split(":", 1)]
        threading.Thread(
            target=serve,
            args=(args.listen_host, local_port, args.target_host, target_port),
            daemon=True,
        ).start()

    threading.Event().wait()


if __name__ == "__main__":
    main()
