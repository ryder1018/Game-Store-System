#!/usr/bin/env python3
"""Lobby server for players and rooms.

Responsibilities:
- Manage player accounts (separate from developer accounts).
- List/ create/ join rooms, and start the underlying game server process by
  consulting the store server for launch info.
- Track simple download manifest per player to check versions before starting.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

from common.framing import recv_json, send_json


def sha256_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _port_alive(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class StoreClient:
    """Tiny RPC helper talking to store_server.py"""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def _call(self, payload: dict) -> dict:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            _ = recv_json(s)  # consume store hello
            send_json(s, payload)
            resp = recv_json(s)
            return resp or {"ok": False, "code": "NO_RESPONSE"}

    def list_games(self):
        return self._call({"op": "list_games"})

    def game_detail(self, gid: str):
        return self._call({"op": "game_detail", "game_id": gid})

    def get_launch_info(self, gid: str, version: Optional[str] = None):
        payload = {"op": "get_launch_info", "game_id": gid}
        if version:
            payload["version"] = version
        return self._call(payload)


class LobbyDB:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.db = json.load(f)
        else:
            self.db = {
                "players": {},  # user -> {passwordHash, createdAt, online, downloads{gid:ver}}
                "rooms": {},    # rid -> {...}
            }
            self._flush()

    def normalize_room(self, rid: str):
        """Reset room status if server already死掉."""
        room = self.db["rooms"].get(rid)
        if not room:
            return None
        srv = room.get("server") or {}
        pid = srv.get("pid")
        if room.get("status") == "playing" and pid and not _pid_alive(pid):
            room["status"] = "idle"
            room["server"] = None
            self._flush()
        return room

    def _flush(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.db, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def save(self):
        with self.lock:
            self._flush()


class LobbySession(threading.Thread):
    def __init__(self, sock, addr, db: LobbyDB, store: StoreClient, game_host: str, game_bind_host: str, port_alloc):
        super().__init__(daemon=True)
        self.s = sock
        self.a = addr
        self.db = db
        self.store = store
        self.game_host = game_host
        self.game_bind_host = game_bind_host
        self.alloc_port = port_alloc
        self.user: Optional[str] = None

    # ---------------------- helpers ----------------------
    def send(self, obj: dict):
        send_json(self.s, obj)

    def require_auth(self) -> bool:
        if not self.user:
            self.send({"ok": False, "code": "AUTH_REQUIRED"})
            return False
        return True

    def run(self):
        try:
            self.send({"ok": True, "code": "HELLO", "msg": "Lobby ready"})
            while True:
                req = recv_json(self.s)
                if not req:
                    break
                op = req.get("op")
                if op == "register":
                    u, p = req.get("user", ""), req.get("password", "")
                    with self.db.lock:
                        if u in self.db.db["players"]:
                            self.send({"ok": False, "code": "USER_EXISTS"}); continue
                        self.db.db["players"][u] = {
                            "passwordHash": sha256_hex(p),
                            "createdAt": int(time.time()),
                            "online": False,
                            "downloads": {},
                        }
                        self.db._flush()
                    self.send({"ok": True, "code": "REGISTERED"})

                elif op == "login":
                    u, p = req.get("user", ""), req.get("password", "")
                    with self.db.lock:
                        doc = self.db.db["players"].get(u)
                        if not doc or doc.get("passwordHash") != sha256_hex(p):
                            self.send({"ok": False, "code": "AUTH_FAILED"}); continue
                        doc["online"] = True
                        doc["lastLoginAt"] = int(time.time())
                        self.db._flush()
                        self.user = u
                    self.send({"ok": True, "code": "LOGIN_SUCCESS", "user": u})

                elif op == "logout":
                    if self.user:
                        with self.db.lock:
                            doc = self.db.db["players"].get(self.user)
                            if doc:
                                doc["online"] = False
                                self.db._flush()
                    self.user = None
                    self.send({"ok": True, "code": "LOGOUT"})

                elif op == "list_players":
                    with self.db.lock:
                        users = []
                        for name, doc in self.db.db["players"].items():
                            users.append({"user": name, "online": bool(doc.get("online")), "lastLoginAt": doc.get("lastLoginAt", 0)})
                    self.send({"ok": True, "code": "PLAYERS", "players": users})

                elif op == "list_rooms":
                    with self.db.lock:
                        rooms = []
                        for rid in list(self.db.db["rooms"].keys()):
                            r = self.db.normalize_room(rid)
                            if r:
                                rooms.append(r)
                    self.send({"ok": True, "code": "ROOMS", "rooms": rooms})

                elif op == "room_info":
                    rid = req.get("room")
                    with self.db.lock:
                        room = self.db.normalize_room(rid)
                    if not room:
                        self.send({"ok": False, "code": "NO_SUCH_ROOM"}); continue
                    self.send({"ok": True, "code": "ROOM", "room": room})

                elif op == "record_download":
                    if not self.require_auth():
                        continue
                    gid = req.get("game_id")
                    version = req.get("version")
                    if not gid or not version:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    with self.db.lock:
                        doc = self.db.db["players"].get(self.user)
                        if not doc:
                            self.send({"ok": False, "code": "AUTH_LOST"}); continue
                        doc.setdefault("downloads", {})[gid] = version
                        self.db._flush()
                    self.send({"ok": True, "code": "RECORDED"})

                elif op == "create_room":
                    if not self.require_auth():
                        continue
                    rid = req.get("room") or f"room-{int(time.time())}"
                    gid = req.get("game_id")
                    if not gid:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    lresp = self.store.game_detail(gid)
                    if not lresp.get("ok"):
                        self.send({"ok": False, "code": "GAME_NOT_FOUND"}); continue
                    g = lresp.get("game", {})
                    version = g.get("latestVersion") or (g.get("versions") or [{}])[-1].get("version")
                    max_players = g.get("maxPlayers", 2)
                    with self.db.lock:
                        if rid in self.db.db["rooms"]:
                            self.send({"ok": False, "code": "ROOM_EXISTS"}); continue
                        room = {
                            "id": rid,
                            "host": self.user,
                            "members": [self.user],
                            "game_id": gid,
                            "game_version": version,
                            "status": "idle",
                            "max_players": max_players,
                        }
                        self.db.db["rooms"][rid] = room
                        self.db._flush()
                    self.send({"ok": True, "code": "ROOM_CREATED", "room": room})

                elif op == "join_room":
                    if not self.require_auth():
                        continue
                    rid = req.get("room")
                    if not rid:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    with self.db.lock:
                        room = self.db.normalize_room(rid)
                        if not room:
                            self.send({"ok": False, "code": "NO_SUCH_ROOM"}); continue
                        # 若房間標示 playing 但 game server 已死，重置為 idle
                        srv = room.get("server") or {}
                        pid = srv.get("pid")
                        if room.get("status") == "playing" and pid and not _pid_alive(pid):
                            room["status"] = "idle"
                            room["server"] = None
                            self.db._flush()
                        members = list(room.get("members", []))
                        if self.user in members:
                            self.send({"ok": True, "code": "JOINED", "room": room}); continue
                        if room.get("status") == "playing":
                            self.send({"ok": False, "code": "IN_GAME"}); continue
                        if len(members) >= int(room.get("max_players", 2)):
                            self.send({"ok": False, "code": "ROOM_FULL"}); continue
                        members.append(self.user)
                        room["members"] = members
                        self.db._flush()
                    self.send({"ok": True, "code": "JOINED", "room": room})

                elif op == "leave_room":
                    if not self.require_auth():
                        continue
                    rid = req.get("room")
                    with self.db.lock:
                        room = self.db.db["rooms"].get(rid)
                        if not room:
                            self.send({"ok": False, "code": "NO_SUCH_ROOM"}); continue
                        members = [m for m in room.get("members", []) if m != self.user]
                        room["members"] = members
                        if room.get("host") == self.user:
                            room["host"] = members[0] if members else None
                        if not members:
                            del self.db.db["rooms"][rid]
                        self.db._flush()
                    self.send({"ok": True, "code": "LEFT"})

                elif op == "start_room":
                    if not self.require_auth():
                        continue
                    rid = req.get("room")
                    with self.db.lock:
                        room = self.db.normalize_room(rid)
                        if not room:
                            self.send({"ok": False, "code": "NO_SUCH_ROOM"}); continue
                        if room.get("host") != self.user:
                            self.send({"ok": False, "code": "NOT_HOST"}); continue
                        if room.get("status") == "playing":
                            srv = room.get("server") or {}
                            pid = srv.get("pid")
                            if pid and _pid_alive(pid):
                                self.send({"ok": True, "code": "ALREADY_PLAYING", "room": room}); continue
                            # server died -> reset
                            room["status"] = "idle"
                            room["server"] = None
                            self.db._flush()
                        members = list(room.get("members", []))
                        if not members:
                            self.send({"ok": False, "code": "EMPTY_ROOM"}); continue
                        if len(members) < 2:
                            self.send({"ok": False, "code": "NEED_TWO_PLAYERS"}); continue
                        gid = room.get("game_id")
                        gver = room.get("game_version")
                    # fetch launch info outside lock
                    lresp = self.store.get_launch_info(gid, gver)
                    if not lresp.get("ok"):
                        # fallback: try latest version once if missing/null
                        if gver:
                            self.send({"ok": False, "code": lresp.get("code", "LAUNCH_FAIL")}); continue
                        alt = self.store.get_launch_info(gid, None)
                        if not alt.get("ok"):
                            self.send({"ok": False, "code": alt.get("code", "LAUNCH_FAIL")}); continue
                        lresp = alt
                        with self.db.lock:
                            room = self.db.db["rooms"].get(rid)
                            if room:
                                room["game_version"] = lresp["info"]["version"]
                                self.db._flush()
                    info = lresp.get("info", {})
                    min_players = int(info.get("min_players", 2))
                    if len(members) < min_players:
                        self.send({"ok": False, "code": "NEED_MIN_PLAYERS", "required": min_players}); continue
                    port = self.alloc_port()
                    cmd = [
                        sys.executable,
                        info.get("server_entry", "server.py"),
                        "--host", self.game_bind_host,
                        "--port", str(port),
                        "--room", rid,
                        "--players", ",".join(members),
                    ]
                    env = os.environ.copy()
                    env["PYTHONPATH"] = info.get("path", ".") + os.pathsep + env.get("PYTHONPATH", "")
                    try:
                        p = subprocess.Popen(
                            cmd,
                            cwd=info.get("path"),
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except OSError as exc:
                        self.send({"ok": False, "code": "SPAWN_FAIL", "msg": str(exc)}); continue
                    # wait briefly to ensure process stays alive / port binds
                    time.sleep(0.5)
                    rc = p.poll()
                    if rc is not None:
                        self.send({"ok": False, "code": "GAME_NOT_READY", "returncode": rc}); continue
                    with self.db.lock:
                        room = self.db.db["rooms"].get(rid)
                        if room:
                            room["status"] = "playing"
                            room["server"] = {"host": self.game_host, "port": port, "pid": p.pid}
                            self.db._flush()
                    # 背景監看：結束後自動把房間狀態設回 idle
                    def _watch(pid: int, rid: str):
                        p.wait()
                        with self.db.lock:
                            r = self.db.db["rooms"].get(rid)
                            if r:
                                r["status"] = "idle"
                                r["server"] = None
                                self.db._flush()
                    threading.Thread(target=_watch, args=(p.pid, rid), daemon=True).start()
                    self.send({"ok": True, "code": "GAME_STARTED", "server": {"host": self.game_host, "port": port}})

                else:
                    self.send({"ok": False, "code": "UNKNOWN_OP"})
        finally:
            try:
                self.s.close()
            except Exception:
                pass


class LobbyServer:
    def __init__(self, host: str, port: int, db_path: str, store_host: str, store_port: int, game_host: str, game_bind_host: str, game_port_start: int):
        self.host = host
        self.port = port
        self.db = LobbyDB(db_path)
        self.store = StoreClient(store_host, store_port)
        self.game_host = game_host
        self.game_bind_host = game_bind_host
        self.next_port = game_port_start
        self.lock = threading.RLock()

    def alloc_port(self) -> int:
        with self.lock:
            p = self.next_port
            self.next_port += 1
            if self.next_port > 65000:
                self.next_port = p + 1  # simple wrap
            return p

    def serve(self):
        print(f"[LOBBY] {self.host}:{self.port} (store={self.store.host}:{self.store.port})")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port)); srv.listen(128)
            while True:
                c, a = srv.accept()
                LobbySession(c, a, self.db, self.store, self.game_host, self.game_bind_host, self.alloc_port).start()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--db", dest="db_path", default="lobby_db.json")
    ap.add_argument("--store_host", default="127.0.0.1")
    ap.add_argument("--store_port", type=int, default=17080)
    ap.add_argument("--game_host", default="127.0.0.1")
    ap.add_argument("--game_bind_host", default="0.0.0.0")
    ap.add_argument("--game_port_start", type=int, default=19100)
    args = ap.parse_args()
    LobbyServer(args.host, args.port, args.db_path, args.store_host, args.store_port, args.game_host, args.game_bind_host, args.game_port_start).serve()
