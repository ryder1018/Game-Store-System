#!/usr/bin/env python3
"""Dice Race Party server: 3-4 players take turns rolling to reach 30 points."""
import argparse
import json
import random
import socket
import threading
import time


def send_json(sock: socket.socket, obj: dict):
    data = json.dumps(obj).encode("utf-8")
    hdr = len(data).to_bytes(4, "big")
    sock.sendall(hdr + data)


def recv_json(sock: socket.socket):
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


class PlayerConn(threading.Thread):
    def __init__(self, sock, addr):
        super().__init__(daemon=True)
        self.s = sock
        self.a = addr
        self.user = None
        self.inbox = []
        self.lock = threading.RLock()

    def send(self, obj: dict):
        try:
            send_json(self.s, obj)
        except OSError:
            pass

    def run(self):
        try:
            hello = recv_json(self.s)
            if not hello:
                return
            self.user = hello.get("user") or f"player-{self.a[1]}"
            self.send({"op": "msg", "text": f"Welcome {self.user}"})
            while True:
                data = recv_json(self.s)
                if not data:
                    break
                with self.lock:
                    self.inbox.append(data)
        finally:
            try:
                self.s.close()
            except OSError:
                pass

    def pop_msg(self, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.inbox:
                    return self.inbox.pop(0)
            time.sleep(0.05)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=19100)
    ap.add_argument("--room", default="room")
    ap.add_argument("--players", default="")
    args = ap.parse_args()

    expected_players = [p for p in args.players.split(",") if p]
    expected = max(2, len(expected_players) if expected_players else 0)
    if expected > 4:
        expected = 4
    print(f"[GAME] Dice Race Party room={args.room} waiting for {expected} players on {args.host}:{args.port}")

    clients: list[PlayerConn] = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((args.host, args.port)); srv.listen(8)
        deadline = time.time() + 25
        while len(clients) < expected and time.time() < deadline:
            try:
                srv.settimeout(1.0)
                c, a = srv.accept()
            except socket.timeout:
                continue
            p = PlayerConn(c, a)
            p.start()
            clients.append(p)

    clients = [c for c in clients if c.user]
    if len(clients) < 2:
        print("[GAME] not enough players (need >=2), closing")
        return
    print("[GAME] all players connected:", [c.user for c in clients])
    target = 15
    for c in clients:
        c.send({"op": "msg", "text": f"房間 {args.room} 開始！輪流擲骰，先達 {target} 分獲勝。"})

    scores = {c.user: 0 for c in clients}
    turn = 0
    winner = None
    while not winner and turn < 200:
        player = clients[turn % len(clients)]
        player.send({"op": "your_turn"})
        msg = player.pop_msg(timeout=20.0)
        if not msg:
            player.send({"op": "msg", "text": "超時跳過這回合"})
            turn += 1
            continue
        if msg.get("op") != "roll":
            player.send({"op": "msg", "text": "請輸入 roll 以擲骰"})
            turn += 1
            continue
        roll = random.randint(1, 6)
        scores[player.user] += roll
        for c in clients:
            c.send({"op": "msg", "text": f"{player.user} 擲出 {roll} 點，總分 {scores[player.user]} / {target}"})
        if scores[player.user] >= target:
            winner = player.user
            break
        turn += 1

    if winner:
        for c in clients:
            c.send({"op": "msg", "text": f"{winner} 抵達 30 分獲勝！"})
    else:
        for c in clients:
            c.send({"op": "msg", "text": "時間到，無人達標"} )
    for c in clients:
        c.send({"op": "end"})
    print("[GAME] finished")


if __name__ == "__main__":
    main()
