#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tetris Battle pygame client for HW3 (2P)."""
import argparse, pygame, socket, threading, time, json
from proto import rle_decode_rowmajor, BOARD_W, BOARD_H
from ui_fx import draw_header, draw_block_cell, FlashOverlay, ScreenShake, Confetti

# --- rendering helpers ---
SHAPE_COLOR = {"I":1,"O":2,"T":3,"S":4,"Z":5,"J":6,"L":7}
_input_seq = 0

_SHAPE_MASKS = {
    "I": [[0,0,0,0],[1,1,1,1],[0,0,0,0],[0,0,0,0]],
    "O": [[0,1,1,0],[0,1,1,0],[0,0,0,0],[0,0,0,0]],
    "T": [[0,1,0,0],[1,1,1,0],[0,0,0,0],[0,0,0,0]],
    "S": [[0,1,1,0],[1,1,0,0],[0,0,0,0],[0,0,0,0]],
    "Z": [[1,1,0,0],[0,1,1,0],[0,0,0,0],[0,0,0,0]],
    "J": [[1,0,0,0],[1,1,1,0],[0,0,0,0],[0,0,0,0]],
    "L": [[0,0,1,0],[1,1,1,0],[0,0,0,0],[0,0,0,0]],
}
def _rot_cw(mask):
    return [[mask[3-x][y] for x in range(4)] for y in range(4)]
def _shape_mask(shape, rot):
    m = _SHAPE_MASKS.get(shape, [[0]*4 for _ in range(4)])
    for _ in range((rot or 0) % 4):
        m = _rot_cw(m)
    return m


# ---------- Game connection ----------
def _connect(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    return s


def connect_game(gh, gp, timeout_sec: float = 10.0):
    deadline = time.time() + timeout_sec
    last_err = None
    while time.time() < deadline:
        try:
            g = _connect(gh, gp)
            hello = recv_json(g)
            welcome = recv_json(g)
            if not welcome or welcome.get("type") != "WELCOME":
                raise RuntimeError(f"bad welcome: {welcome}")
            my_role = welcome.get("role", "P1")
            return g, my_role
        except OSError as e:
            last_err = e
            time.sleep(0.2)
    raise ConnectionError(f"cannot connect to game server {gh}:{gp}: {last_err}")


# ---------- framing ----------
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


# ---------- Pygame UI ----------
CELL = 28
GAP = 2
PADDING = 20
GUTTER_X = 64      # 兩棋盤間的水平留白
HEADER_H = 120
FOOT_H = 44        # 底部結果列高度
FPS = 60

# 色彩
COLORS = [
    (20,20,20),     # empty
    (0,180,255),    # I
    (255,220,0),    # O
    (180,0,255),    # T
    (0,255,100),    # S
    (255,0,100),    # Z
    (0,100,255),    # J
    (255,140,0),    # L
]
BG       = (10,10,10)
GRID     = (40,40,40)
TEXT     = (230,230,230)
TEXT_DIM = (190,190,190)
RESULT_C = (255,220,160)


def draw_grid(surface, ox, oy, grid, active=None, label="YOU", score=0):
    w, h = BOARD_W, BOARD_H
    board_w_px = BOARD_W*(CELL+GAP) - GAP
    board_h_px = BOARD_H*(CELL+GAP) - GAP

    # 棋盤（已鎖定）
    for y in range(h):
        for x in range(w):
            val = grid[y][x]
            color = COLORS[val] if 0 <= val < len(COLORS) else COLORS[0]
            rx = ox + x * (CELL + GAP)
            ry = oy + y * (CELL + GAP)
            if val:
                draw_block_cell(surface, rx, ry, CELL, color)  # 霓虹格
            else:
                pygame.draw.rect(surface, GRID, (rx, ry, CELL, CELL), width=1, border_radius=4)

    # 疊加下落中的 active
    if active and isinstance(active, dict) and active.get("shape"):
        ax, ay = int(active.get("x", 0)), int(active.get("y", 0))
        rot    = int(active.get("rot", 0))
        shape  = active.get("shape", "T")
        color_idx = int(active.get("color", SHAPE_COLOR.get(shape, 1)))
        color = COLORS[color_idx] if 0 <= color_idx < len(COLORS) else COLORS[1]
        mask = _shape_mask(shape, rot)
        for yy in range(4):
            for xx in range(4):
                if not mask[yy][xx]:
                    continue
                gx, gy = ax + xx, ay + yy
                if 0 <= gx < w and 0 <= gy < h:
                    rx = ox + gx * (CELL + GAP)
                    ry = oy + gy * (CELL + GAP)
                    draw_block_cell(surface, rx, ry, CELL, color)

    # 下方兩行：YOU/RIVAL 與 score
    label_font = pygame.font.SysFont(None, 26)
    score_font = pygame.font.SysFont(None, 22)
    surface.blit(label_font.render(label, True, TEXT),
                 (ox, oy + board_w_px + (BOARD_H*(CELL+GAP)-GAP - board_w_px)))
    surface.blit(score_font.render(f"score: {score}", True, TEXT_DIM),
                 (ox, oy + board_h_px + 30))


def blank_grid(): return [[0]*BOARD_W for _ in range(BOARD_H)]


class ClientState:
    def __init__(self, my_role: str):
        self.my_role = my_role
        self.roles = ["P1", "P2"]
        self.lock = threading.RLock()
        self.snap = {
            "P1": {"board": blank_grid(), "active": {"x":5,"y":0,"shape":"T","rot":0}, "score":0, "tick":0},
            "P2": {"board": blank_grid(), "active": {"x":5,"y":0,"shape":"T","rot":0}, "score":0, "tick":0},
        }
        self.result = None

    def update_snap(self, role, snap):
        with self.lock:
            self.snap[role] = snap

    def set_result(self, res):
        with self.lock:
            self.result = res


def start_rx_thread(sock, state: ClientState):
    stop_flag = {"stop": False}

    def rx():
        while not stop_flag["stop"]:
            m = recv_json(sock)
            if not m:
                break
            t = m.get("type")
            if t == "SNAPSHOT":
                board = rle_decode_rowmajor(m.get("boardRLE", ""), BOARD_W, BOARD_H)
                state.update_snap(m.get("role"), {
                    "board": board,
                    "active": m.get("active"),
                    "hold": m.get("hold"),
                    "next": m.get("next", []),
                    "score": m.get("score", 0),
                    "lines": m.get("lines", 0),
                    "tick": m.get("tick", 0),
                })
            elif t == "RESULT":
                state.set_result(m)
                stop_flag["stop"] = True
                break

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    return th, stop_flag


def run_ui(sock, state: ClientState, user_id: str):
    pygame.init()
    board_w_px = BOARD_W * (CELL + GAP) - GAP
    board_h_px = BOARD_H * (CELL + GAP) - GAP
    screen_w = PADDING*2 + board_w_px*2 + GUTTER_X
    screen_h = PADDING*2 + HEADER_H + board_h_px + FOOT_H
    screen = pygame.display.set_mode((screen_w, screen_h))
    pygame.display.set_caption("Tetris Battle")

    font_big = pygame.font.SysFont(None, 42)
    font_mid = pygame.font.SysFont(None, 24)
    clock = pygame.time.Clock()
    shake = ScreenShake()
    confetti = Confetti()
    flash_self = FlashOverlay((100,200,255))
    flash_rival = FlashOverlay((255,180,80))

    me_role = state.my_role
    peer_role = "P2" if me_role == "P1" else "P1"

    prev_lines = {me_role: 0, peer_role: 0}

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_q:
                    return
                elif event.key == pygame.K_LEFT:
                    send_json(sock, {"type": "INPUT", "action": "LEFT", "seq": _next_seq()})
                elif event.key == pygame.K_RIGHT:
                    send_json(sock, {"type": "INPUT", "action": "RIGHT", "seq": _next_seq()})
                elif event.key == pygame.K_UP:
                    send_json(sock, {"type": "INPUT", "action": "ROT", "seq": _next_seq()})
                elif event.key == pygame.K_SPACE:
                    send_json(sock, {"type": "INPUT", "action": "HARD", "seq": _next_seq()})
                elif event.key == pygame.K_DOWN:
                    send_json(sock, {"type": "INPUT", "action": "SOFT", "seq": _next_seq()})
                elif event.key == pygame.K_c:
                    send_json(sock, {"type": "INPUT", "action": "HOLD", "seq": _next_seq()})

        offset = shake.offset()
        screen.fill(BG)
        screen.blit(screen, offset)
        draw_header(screen, "Tetris Battle", f"玩家: {user_id}  角色: {me_role}",
                    pos=(PADDING + offset[0], PADDING + offset[1]))

        me = state.snap.get(me_role, {})
        peer = state.snap.get(peer_role, {})

        top_y = PADDING + HEADER_H + offset[1]
        left_x = PADDING + offset[0]
        right_x = PADDING + board_w_px + GUTTER_X + offset[0]

        draw_grid(screen, left_x, top_y, me.get("board", blank_grid()), me.get("active"), "YOU", me.get("score", 0))
        draw_grid(screen, right_x, top_y, peer.get("board", blank_grid()), peer.get("active"), "RIVAL", peer.get("score", 0))

        # 底部結果
        res = state.result
        result_rect = pygame.Rect(PADDING + offset[0], top_y + board_h_px + 40, board_w_px*2 + GUTTER_X, FOOT_H)
        if res:
            winner = res.get("winner")
            if winner == me_role:
                txt = f"You win! {res.get('p1')} - {res.get('p2')}"
                confetti.burst((screen_w//2, screen_h//2), n=60)
            elif winner == "draw":
                txt = "Draw"
            else:
                txt = f"You lose. {res.get('p1')} - {res.get('p2')}"
            msg = font_big.render(txt, True, RESULT_C)
            screen.blit(msg, (result_rect.x + 12, result_rect.y))
            hint = font_mid.render("Q 退出房間", True, TEXT_DIM)
            screen.blit(hint, (result_rect.x + 12, result_rect.y + 32))

        # 特效
        if me.get("lines", 0) > prev_lines[me_role]:
            flash_self.trigger()
            confetti.burst((left_x + board_w_px//2, top_y + board_h_px//2), n=30)
            prev_lines[me_role] = me["lines"]
        if peer.get("lines", 0) > prev_lines[peer_role]:
            flash_rival.trigger()
            prev_lines[peer_role] = peer["lines"]

        flash_self.draw(screen, pygame.Rect(left_x, top_y, board_w_px, board_h_px))
        flash_rival.draw(screen, pygame.Rect(right_x, top_y, board_w_px, board_h_px))
        confetti.update_draw(screen, clock.get_time()/1000.0)

        pygame.display.flip()
        clock.tick(FPS)


def _next_seq():
    global _input_seq
    _input_seq += 1
    return _input_seq


def main():
    ap = argparse.ArgumentParser(description="HW3 Tetris Battle client (pygame)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=19100)
    ap.add_argument("--user", default="player")
    args = ap.parse_args()

    gsock, role = connect_game(args.host, args.port)
    state = ClientState(role)
    _rx, stop_flag = start_rx_thread(gsock, state)
    try:
        run_ui(gsock, state, args.user)
    finally:
        stop_flag["stop"] = True
        try:
            gsock.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
