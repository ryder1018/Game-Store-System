#!/usr/bin/env python3
"""雙人俄羅斯方塊對戰伺服器，供 HW3 Lobby 自動啟動。

啟動參數由 Lobby 傳入：
  --host 0.0.0.0 --port <port> --room <room_id> --players user1,user2
"""
import argparse
import json
import random
import socket
import threading
import time

from proto import BOARD_W, BOARD_H, rle_encode_rowmajor

# 網路 framing：4 bytes 長度 + JSON
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


TICK_MS = 500
SHAPES = ["I", "O", "T", "S", "Z", "J", "L"]
SHAPE_MASKS = {
    "I": [[0,0,0,0],[1,1,1,1],[0,0,0,0],[0,0,0,0]],
    "O": [[0,1,1,0],[0,1,1,0],[0,0,0,0],[0,0,0,0]],
    "T": [[0,1,0,0],[1,1,1,0],[0,0,0,0],[0,0,0,0]],
    "S": [[0,1,1,0],[1,1,0,0],[0,0,0,0],[0,0,0,0]],
    "Z": [[1,1,0,0],[0,1,1,0],[0,0,0,0],[0,0,0,0]],
    "J": [[1,0,0,0],[1,1,1,0],[0,0,0,0],[0,0,0,0]],
    "L": [[0,0,1,0],[1,1,1,0],[0,0,0,0],[0,0,0,0]],
}
SHAPE_COLOR = {"I":1,"O":2,"T":3,"S":4,"Z":5,"J":6,"L":7}


def rotate_cw(mask):
    return [[mask[3-x][y] for x in range(4)] for y in range(4)]


def shape_mask(shape, rot):
    m = SHAPE_MASKS[shape]
    for _ in range(rot % 4):
        m = rotate_cw(m)
    return m


def fits(board, shape, x, y, rot):
    m = shape_mask(shape, rot)
    for yy in range(4):
        for xx in range(4):
            if not m[yy][xx]:
                continue
            bx = x + xx
            by = y + yy
            if bx < 0 or bx >= BOARD_W or by < 0 or by >= BOARD_H:
                return False
            if board[by][bx] != 0:
                return False
    return True


def lock_piece(board, shape, x, y, rot, color_id):
    m = shape_mask(shape, rot)
    for yy in range(4):
        for xx in range(4):
            if m[yy][xx]:
                board[y+yy][x+xx] = color_id


def clear_full_lines(board):
    new_rows = [row for row in board if any(v == 0 for v in row)]
    cleared = BOARD_H - len(new_rows)
    while len(new_rows) < BOARD_H:
        new_rows.insert(0, [0]*BOARD_W)
    return cleared, new_rows


class SevenBag:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.bag = []

    def _refill(self):
        self.bag = list(SHAPES)
        self.rng.shuffle(self.bag)

    def next(self):
        if not self.bag:
            self._refill()
        return self.bag.pop()


def new_board():
    return [[0]*BOARD_W for _ in range(BOARD_H)]


class Player:
    def __init__(self, sock, role):
        self.s = sock
        self.role = role
        self.board = new_board()
        self.score = 0
        self.lines = 0
        self.active = None
        self.alive = True
        self.hold = None
        self.can_hold = True
        self.next = []

    def spawn_from_queue(self, bag: SevenBag):
        while len(self.next) < 5:
            self.next.append(bag.next())
        shape = self.next.pop(0)
        color_id = SHAPE_COLOR[shape]
        self.active = {"shape": shape, "x": 3, "y": 0, "rot": 0, "color": color_id}
        self.can_hold = True
        if not fits(self.board, shape, self.active["x"], self.active["y"], self.active["rot"]):
            self.alive = False


class Game:
    def __init__(self, seed):
        self.seed = seed
        self.players = {}
        self.lock = threading.RLock()
        self.tick = 0
        self.running = True
        self.bag = SevenBag(seed)
        self.start_ts = int(time.time())

    def add_player(self, role, sock):
        self.players[role] = Player(sock, role)
        self.players[role].spawn_from_queue(self.bag)
        send_json(sock, {
            "type": "WELCOME",
            "role": role,
            "seed": self.seed,
            "bagRule": "7bag",
            "gravityPlan": {"mode": "fixed", "dropMs": TICK_MS}
        })

    def _try_move(self, p: Player, dx=0, dy=0, drot=0):
        if not p.active:
            return False
        a = p.active
        nx, ny, nr = a["x"] + dx, a["y"] + dy, (a["rot"] + drot) % 4
        if fits(p.board, a["shape"], nx, ny, nr):
            a["x"], a["y"], a["rot"] = nx, ny, nr
            return True
        return False

    def _hard_drop(self, p: Player):
        if not p.active:
            return
        while self._try_move(p, dy=1):
            pass
        self._lock_and_refill(p, hard_drop=True)

    def _soft_drop_or_gravity(self, p: Player):
        if not self._try_move(p, dy=1):
            self._lock_and_refill(p, hard_drop=False)

    def _lock_and_refill(self, p: Player, hard_drop=False):
        a = p.active
        if not a:
            return
        lock_piece(p.board, a["shape"], a["x"], a["y"], a["rot"], a["color"])
        p.score += (2 if hard_drop else 1)
        cleared, new_b = clear_full_lines(p.board)
        if cleared > 0:
            p.board = new_b
            p.lines += cleared
            p.score += 100 * cleared
        p.spawn_from_queue(self.bag)

    def handle_input(self, role, msg):
        with self.lock:
            p = self.players.get(role)
            if not p or not p.alive:
                return
            act = msg.get("action")
            if act == "LEFT":
                self._try_move(p, dx=-1)
            elif act == "RIGHT":
                self._try_move(p, dx=1)
            elif act == "SOFT":
                self._soft_drop_or_gravity(p)
            elif act == "HARD":
                self._hard_drop(p)
            elif act == "ROT":
                self._try_move(p, drot=1)
            elif act == "HOLD":
                if p.can_hold and p.active:
                    cur = p.active["shape"]
                    if p.hold is None:
                        p.hold = cur
                        p.spawn_from_queue(self.bag)
                    else:
                        p.hold, p.active["shape"] = cur, p.hold
                        p.active.update({"x":3,"y":0,"rot":0,
                                         "color":SHAPE_COLOR[p.active["shape"]]})
                        if not fits(p.board, p.active["shape"], p.active["x"], p.active["y"], p.active["rot"]):
                            p.alive = False
                    p.can_hold = False

    def step(self):
        with self.lock:
            self.tick += 1
            for p in self.players.values():
                if p.alive:
                    self._soft_drop_or_gravity(p)
            # 縮短對局時間，預設 15 秒結束
            if int(time.time()) - self.start_ts >= 15:
                self.running = False
            if self.players and all(not pl.alive for pl in self.players.values()):
                self.running = False

    def snapshots(self):
        with self.lock:
            snaps = []
            for role, p in self.players.items():
                snaps.append({
                    "type": "SNAPSHOT",
                    "tick": self.tick,
                    "role": role,
                    "userId": role,
                    "boardRLE": rle_encode_rowmajor(p.board),
                    "active": {"shape": p.active["shape"], "x": p.active["x"], "y": p.active["y"], "rot": p.active["rot"]} if p.active else None,
                    "hold": p.hold,
                    "next": list(p.next[:3]),
                    "score": p.score,
                    "lines": p.lines,
                    "level": 1,
                    "at": int(time.time() * 1000)
                })
            return snaps


def serve_game(host, port, room, users_csv=""):
    users = [u for u in (users_csv.split(",") if users_csv else []) if u]
    g = Game(seed=random.randrange(1 << 31))
    print(f"[GAME] {host}:{port} room={room} users={users}", flush=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port)); srv.listen(2)

        def accept_player(role):
            while True:
                c, _ = srv.accept()
                try:
                    send_json(c, {"type": "HELLO", "version": 1, "roomId": room})
                    g.add_player(role, c)
                    return c
                except OSError:
                    try: c.close()
                    except: pass
                    continue

        c1 = accept_player("P1")
        c2 = accept_player("P2")

        def rx(role, sock):
            try:
                while g.running:
                    msg = recv_json(sock)
                    if not msg:
                        break
                    if msg.get("type") == "INPUT":
                        g.handle_input(role, msg)
            finally:
                try: sock.close()
                except: pass

        threading.Thread(target=rx, args=("P1", c1), daemon=True).start()
        threading.Thread(target=rx, args=("P2", c2), daemon=True).start()

        next_tick = time.time()
        while g.running:
            now = time.time()
            if now >= next_tick:
                g.step()
                snaps = g.snapshots()
                for p in list(g.players.values()):
                    for s in snaps:
                        try: send_json(p.s, s)
                        except OSError:
                            pass
                next_tick += TICK_MS / 1000.0
            time.sleep(0.005)

        # 結算
        p1, p2 = g.players["P1"], g.players["P2"]
        winner = "draw" if p1.score == p2.score else ("P1" if p1.score > p2.score else "P2")
        result = {"type": "RESULT", "winner": winner, "p1": p1.score, "p2": p2.score}
        for p in g.players.values():
            try: send_json(p.s, result)
            except OSError:
                pass


def _positive_int(v):
    try:
        x = int(v)
        if x <= 0:
            raise ValueError
        return x
    except Exception:
        raise argparse.ArgumentTypeError("must be positive int")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=_positive_int, default=19100)
    ap.add_argument("--room", default="room")
    ap.add_argument("--players", default="")
    args = ap.parse_args()
    serve_game(args.host, args.port, args.room, args.players)
