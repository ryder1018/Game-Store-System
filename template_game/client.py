#!/usr/bin/env python3
"""Number Battle client."""
import argparse
import json
import socket
import sys


def send_json(sock: socket.socket, obj: dict):
    data = json.dumps(obj).encode("utf-8")
    hdr = len(data).to_bytes(4, "big")
    sock.sendall(hdr + data)


def recv_json(sock: socket.socket) -> dict | None:
    hdr = sock.recv(4)
    if not hdr:
        return None
    n = int.from_bytes(hdr, "big")
    body = b""
    while len(body) < n:
        chunk = sock.recv(n - len(body))
        if not chunk:
            return None
        body += chunk
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=19100)
    ap.add_argument("--user", default="player")
    args = ap.parse_args()

    with socket.create_connection((args.host, args.port)) as s:
        send_json(s, {"op": "hello", "user": args.user})
        print(f"已連線到遊戲 {args.host}:{args.port}，玩家 {args.user}")
        while True:
            msg = recv_json(s)
            if not msg:
                break
            op = msg.get("op")
            if op == "msg":
                print(msg.get("text", ""))
            elif op == "your_turn":
                try:
                    guess = int(input("請猜 1-50 整數: ").strip())
                except ValueError:
                    guess = 0
                send_json(s, {"op": "guess", "guess": guess})
            elif op == "end":
                print("遊戲結束")
                break
    print("連線結束")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
