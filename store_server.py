#!/usr/bin/env python3
"""Developer / Store server for HW3.

This process exposes a framed-JSON socket API that both developers and players
use:
- Developers: register/login, upload new games, push new versions, remove games.
- Players / Lobby: list games, fetch details, download archives, fetch launch
  info for game servers, submit ratings after playing.

Files uploaded by developers are stored under ``uploaded_games/<game_id>/<ver>``
where both the original zip and an extracted directory are kept. The extracted
dir is what the lobby server uses to spawn the game server entrypoint.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import threading
import time

from common.framing import recv_json, send_json


def validate_game_bundle(gdir: str) -> tuple[bool, str, dict]:
    """Basic validation to防呆 developer上傳內容."""
    cfg_path = os.path.join(gdir, "game_config.json")
    if not os.path.exists(cfg_path):
        return False, "CONFIG_MISSING", {"missing": ["game_config.json"]}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return False, "CONFIG_INVALID_JSON", {}
    missing_fields = [k for k in ("server_entry", "client_entry") if k not in cfg]
    if missing_fields:
        return False, "CONFIG_FIELDS_MISSING", {"missing": missing_fields}
    missing_files = []
    for key in ("server_entry", "client_entry"):
        entry = cfg.get(key)
        if entry:
            ep = os.path.join(gdir, entry)
            if not os.path.exists(ep):
                missing_files.append(entry)
    if missing_files:
        return False, "ENTRY_NOT_FOUND", {"missing_files": missing_files}
    return True, "OK", {"config": cfg}


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "game"


class StoreDB:
    """Very small JSON-backed store for developers, games, and ratings."""

    def __init__(self, db_path: str, storage_root: str):
        self.db_path = db_path
        self.storage_root = storage_root
        os.makedirs(storage_root, exist_ok=True)
        self.lock = threading.RLock()
        # runtime-only session tokens to避免重複登入，同帳號僅允許最後一次登入有效
        self.active_dev_sessions: dict[str, str] = {}
        if os.path.exists(db_path):
            with open(db_path, "r", encoding="utf-8") as f:
                self.db = json.load(f)
        else:
            self.db = {
                "developers": {},  # user -> {passwordHash, createdAt}
                "games": {},       # game_id -> {...}
                "player_downloads": {},  # player -> {game_id: version}
            }
            self._flush()

    # ---------------------- persistence helpers ----------------------
    def _flush(self):
        tmp = self.db_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.db, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.db_path)

    def _save(self):
        with self.lock:
            self._flush()

    # ---------------------- developer accounts -----------------------
    def register_dev(self, user: str, password: str):
        with self.lock:
            if user in self.db["developers"]:
                return False, "USER_EXISTS"
            self.db["developers"][user] = {
                "passwordHash": sha256_hex(password),
                "createdAt": int(time.time()),
            }
            self._flush()
            return True, "REGISTERED"

    def login_dev(self, user: str, password: str):
        with self.lock:
            doc = self.db["developers"].get(user)
            if not doc:
                return False, "NO_SUCH_USER"
            if doc.get("passwordHash") != sha256_hex(password):
                return False, "BAD_CREDENTIALS"
            return True, "LOGIN_OK"

    # ---------------------- session helpers -------------------------
    def new_dev_session(self, user: str) -> tuple[str, bool]:
        """Create a new session token; returns (token, replaced_existing)."""
        token = f"{time.time_ns()}-{os.getpid()}-{threading.get_ident()}"
        with self.lock:
            replaced = user in self.active_dev_sessions
            self.active_dev_sessions[user] = token
        return token, replaced

    def validate_dev_session(self, user: str | None, token: str | None) -> bool:
        if not user or not token:
            return False
        with self.lock:
            return self.active_dev_sessions.get(user) == token

    def clear_dev_session(self, user: str | None, token: str | None):
        if not user or not token:
            return
        with self.lock:
            if self.active_dev_sessions.get(user) == token:
                self.active_dev_sessions.pop(user, None)

    # ---------------------- game helpers -----------------------------
    def list_games(self, author: str | None = None, include_removed=False, include_versions=False):
        with self.lock:
            items = []
            for gid, g in self.db["games"].items():
                if author and g.get("author") != author:
                    continue
                if g.get("removed") and not include_removed:
                    continue
                item = self._public_game_info(gid, g)
                if include_versions:
                    item["versions"] = g.get("versions", [])
                items.append(item)
            return items

    def _public_game_info(self, gid: str, gdoc: dict) -> dict:
        versions = gdoc.get("versions") or []
        latest = gdoc.get("latestVersion") or (versions[-1]["version"] if versions else "")
        ratings = gdoc.get("ratings") or []
        avg = round(sum(r["score"] for r in ratings) / len(ratings), 2) if ratings else 0
        return {
            "id": gid,
            "name": gdoc.get("name", gid),
            "description": gdoc.get("description", ""),
            "author": gdoc.get("author", ""),
            "gameType": gdoc.get("gameType", "cli"),
            "maxPlayers": gdoc.get("maxPlayers", 2),
            "latestVersion": latest,
            "versionCount": len(versions),
            "removed": gdoc.get("removed", False),
            "ratingAvg": avg,
            "ratingCount": len(ratings),
            "downloadCount": gdoc.get("downloadCount", 0),
        }

    def ensure_game_dir(self, gid: str, version: str):
        gdir = os.path.abspath(os.path.join(self.storage_root, gid, version))
        os.makedirs(gdir, exist_ok=True)
        return gdir

    def record_rating(self, player: str, gid: str, score: int, comment: str):
        with self.lock:
            g = self.db["games"].get(gid)
            if not g or g.get("removed"):
                return False, "NO_SUCH_GAME"
            if score < 1 or score > 5:
                return False, "BAD_SCORE"
            dl = self.db["player_downloads"].get(player, {})
            if gid not in dl:
                return False, "NEED_DOWNLOAD_FIRST"
            g.setdefault("ratings", []).append({
                "user": player,
                "score": score,
                "comment": comment,
                "at": int(time.time()),
            })
            self._flush()
            return True, "RATED"

    def record_download(self, player: str, gid: str, version: str):
        with self.lock:
            dl = self.db["player_downloads"].setdefault(player, {})
            dl[gid] = version
            g = self.db["games"].get(gid)
            if g:
                g["downloadCount"] = g.get("downloadCount", 0) + 1
            self._flush()


class StoreSession(threading.Thread):
    def __init__(self, sock, addr, db: StoreDB):
        super().__init__(daemon=True)
        self.s = sock
        self.a = addr
        self.db = db
        self.dev_user: str | None = None
        self.dev_session_token: str | None = None

    # --------------------------- helpers ---------------------------
    def send(self, obj: dict):
        try:
            send_json(self.s, obj)
            return True
        except OSError:
            # Client already closed; stop sending to avoid BrokenPipe stack traces
            return False

    def require_dev_session(self) -> bool:
        """Validate developer login + active session token (防止重複登入)."""
        if not self.dev_user or not self.dev_session_token:
            self.send({"ok": False, "code": "AUTH_REQUIRED"})
            return False
        if not self.db.validate_dev_session(self.dev_user, self.dev_session_token):
            # someone else已經以同帳號重新登入
            self.dev_user = None
            self.dev_session_token = None
            self.send({"ok": False, "code": "SESSION_EXPIRED"})
            return False
        return True

    def run(self):
        try:
            self.send({"ok": True, "code": "HELLO", "msg": "Store server ready"})
            while True:
                req = recv_json(self.s)
                if not req:
                    break
                op = req.get("op")
                if op == "ping":
                    self.send({"ok": True, "code": "PONG"})
                elif op == "dev_register":
                    u, p = req.get("user", ""), req.get("password", "")
                    ok, code = self.db.register_dev(u, p)
                    self.send({"ok": ok, "code": code})
                elif op == "dev_login":
                    u, p = req.get("user", ""), req.get("password", "")
                    ok, code = self.db.login_dev(u, p)
                    if ok:
                        self.dev_user = u
                        token, replaced = self.db.new_dev_session(u)
                        self.dev_session_token = token
                        self.send({"ok": ok, "code": code, "user": u, "session_replaced": replaced})
                    else:
                        self.send({"ok": ok, "code": code, "user": None})
                elif op == "dev_list":
                    if not self.require_dev_session():
                        continue
                    games = self.db.list_games(author=self.dev_user, include_removed=True, include_versions=True)
                    self.send({"ok": True, "code": "MY_GAMES", "games": games})
                elif op == "dev_remove":
                    if not self.require_dev_session():
                        continue
                    gid = req.get("game_id")
                    if not gid:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    with self.db.lock:
                        g = self.db.db["games"].get(gid)
                        if not g:
                            self.send({"ok": False, "code": "NO_SUCH_GAME"}); continue
                        if g.get("author") != self.dev_user:
                            self.send({"ok": False, "code": "NOT_OWNER"}); continue
                        g["removed"] = True
                        self.db._flush()
                        self.send({"ok": True, "code": "REMOVED"})
                elif op == "dev_upload":
                    if not self.require_dev_session():
                        continue
                    raw_name = req.get("name") or ""
                    gid = req.get("game_id") or slugify(raw_name)
                    version = req.get("version") or f"v{int(time.time())}"
                    desc = req.get("description", "")
                    game_type = req.get("game_type", "cli")
                    try:
                        max_players = int(req.get("max_players", 2))
                    except (TypeError, ValueError):
                        self.send({"ok": False, "code": "BAD_FIELD", "field": "max_players"}); continue
                    archive_b64 = req.get("archive_b64")
                    if not archive_b64:
                        self.send({"ok": False, "code": "NO_ARCHIVE"}); continue

                    try:
                        blob = base64.b64decode(archive_b64.encode("utf-8"))
                    except Exception:
                        self.send({"ok": False, "code": "BAD_ARCHIVE"}); continue

                    # persist archive
                    gdir = self.db.ensure_game_dir(gid, version)
                    zip_path = os.path.abspath(os.path.join(self.db.storage_root, gid, f"{version}.zip"))
                    with open(zip_path, "wb") as f:
                        f.write(blob)
                    # extract to version dir
                    try:
                        shutil.unpack_archive(zip_path, gdir)
                    except Exception:
                        self.send({"ok": False, "code": "UNPACK_FAIL"}); continue
                    valid, vcode, vdetail = validate_game_bundle(gdir)
                    if not valid:
                        # 清掉無效上傳，避免留下半套資料
                        try:
                            shutil.rmtree(gdir, ignore_errors=True)
                            os.remove(zip_path)
                        except OSError:
                            pass
                        payload = {"ok": False, "code": vcode}
                        payload.update(vdetail)
                        self.send(payload)
                        continue
                    cfg = vdetail.get("config", {})
                    raw_name = raw_name or cfg.get("name", gid)
                    desc = desc or cfg.get("description", "")
                    game_type = game_type or cfg.get("type", "cli")
                    if not max_players:
                        try:
                            max_players = int(cfg.get("max_players", 2))
                        except Exception:
                            max_players = 2
                    if max_players < 1:
                        self.send({"ok": False, "code": "BAD_FIELD", "field": "max_players"}); continue

                    with self.db.lock:
                        games = self.db.db["games"]
                        gdoc = games.get(gid)
                        now = int(time.time())
                        if not gdoc:
                            gdoc = {
                                "id": gid,
                                "name": raw_name or gid,
                                "author": self.dev_user,
                                "description": desc,
                                "gameType": game_type,
                                "maxPlayers": max_players,
                                "versions": [],
                                "latestVersion": version,
                                "removed": False,
                                "ratings": [],
                                "downloadCount": 0,
                            }
                            games[gid] = gdoc
                        else:
                            if gdoc.get("author") != self.dev_user:
                                self.send({"ok": False, "code": "NOT_OWNER"}); continue
                            if any(v.get("version") == version for v in gdoc.get("versions", [])):
                                self.send({"ok": False, "code": "VERSION_EXISTS"}); continue
                            gdoc["name"] = raw_name or gdoc.get("name", gid)
                            gdoc["description"] = desc or gdoc.get("description", "")
                            gdoc["gameType"] = game_type or gdoc.get("gameType", "cli")
                            gdoc["maxPlayers"] = max_players or gdoc.get("maxPlayers", 2)
                            gdoc["removed"] = False
                            gdoc["latestVersion"] = version
                        gdoc.setdefault("versions", []).append({
                            "version": version,
                            "path": gdir,
                            "zip": zip_path,
                            "uploadedAt": now,
                        })
                        self.db._flush()
                    self.send({"ok": True, "code": "UPLOADED", "game_id": gid, "version": version})
                elif op == "list_games":
                    games = self.db.list_games(author=None, include_removed=False)
                    self.send({"ok": True, "code": "GAMES", "games": games})
                elif op == "game_detail":
                    gid = req.get("game_id")
                    if not gid:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    with self.db.lock:
                        g = self.db.db["games"].get(gid)
                        if not g or g.get("removed"):
                            self.send({"ok": False, "code": "NO_SUCH_GAME"}); continue
                        detail = dict(self.db._public_game_info(gid, g))
                        detail["versions"] = g.get("versions", [])
                        detail["ratings"] = g.get("ratings", [])
                    self.send({"ok": True, "code": "GAME", "game": detail})
                elif op == "download_game":
                    gid = req.get("game_id")
                    version = req.get("version")
                    player = req.get("player", "unknown")
                    if not gid:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    with self.db.lock:
                        g = self.db.db["games"].get(gid)
                        if not g or g.get("removed"):
                            self.send({"ok": False, "code": "NO_SUCH_GAME"}); continue
                        versions = g.get("versions", [])
                        if not versions:
                            self.send({"ok": False, "code": "NO_VERSION"}); continue
                        if not version:
                            version = g.get("latestVersion") or versions[-1]["version"]
                        target = None
                        for v in versions:
                            if v.get("version") == version:
                                target = v; break
                        if not target:
                            self.send({"ok": False, "code": "NO_SUCH_VERSION"}); continue
                        zip_path = target.get("zip")
                    try:
                        with open(zip_path, "rb") as f:
                            blob = f.read()
                    except OSError:
                        self.send({"ok": False, "code": "FILE_MISSING"}); continue
                    self.db.record_download(player, gid, version)
                    self.send({
                        "ok": True,
                        "code": "DOWNLOAD",
                        "game_id": gid,
                        "version": version,
                        "archive_b64": base64.b64encode(blob).decode("utf-8"),
                    })
                elif op == "record_rating":
                    gid = req.get("game_id")
                    player = req.get("player", "")
                    score = int(req.get("score", 0))
                    comment = req.get("comment", "")
                    ok, code = self.db.record_rating(player, gid, score, comment)
                    self.send({"ok": ok, "code": code})
                elif op == "get_launch_info":
                    gid = req.get("game_id")
                    version = req.get("version")
                    if not gid:
                        self.send({"ok": False, "code": "BAD_FIELD"}); continue
                    with self.db.lock:
                        g = self.db.db["games"].get(gid)
                        if not g or g.get("removed"):
                            self.send({"ok": False, "code": "NO_SUCH_GAME"}); continue
                        versions = g.get("versions", [])
                        if not versions:
                            self.send({"ok": False, "code": "NO_VERSION"}); continue
                        if not version:
                            version = g.get("latestVersion") or versions[-1]["version"]
                        target = None
                        for v in versions:
                            if v.get("version") == version:
                                target = v; break
                        if not target:
                            self.send({"ok": False, "code": "NO_SUCH_VERSION"}); continue
                        path = os.path.abspath(target.get("path", ""))
                        cfg_path = os.path.join(path, "game_config.json")
                        cfg = {}
                        if os.path.exists(cfg_path):
                            try:
                                with open(cfg_path, "r", encoding="utf-8") as f:
                                    cfg = json.load(f)
                            except Exception:
                                cfg = {}
                        info = {
                            "game_id": gid,
                            "version": version,
                            "path": path,
                            "server_entry": cfg.get("server_entry", "server.py"),
                            "client_entry": cfg.get("client_entry", "client.py"),
                            "game_type": g.get("gameType", "cli"),
                            "max_players": g.get("maxPlayers", 2),
                            "min_players": int(cfg.get("min_players", 2)),
                            "name": g.get("name", gid),
                        }
                    self.send({"ok": True, "code": "LAUNCH_INFO", "info": info})
                else:
                    self.send({"ok": False, "code": "UNKNOWN_OP"})
        finally:
            try:
                self.db.clear_dev_session(self.dev_user, self.dev_session_token)
            except Exception:
                pass
            try:
                self.s.close()
            except Exception:
                pass


def serve(host: str, port: int, db_path: str, storage_root: str):
    db = StoreDB(db_path, storage_root)
    print(f"[STORE] listening on {host}:{port}, storage={storage_root}, db={db_path}")
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port)); srv.listen(128)
        while True:
            c, a = srv.accept()
            StoreSession(c, a, db).start()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=17080)
    ap.add_argument("--db", dest="db_path", default="store_db.json")
    ap.add_argument("--storage_root", default="uploaded_games")
    args = ap.parse_args()
    serve(args.host, args.port, args.db_path, args.storage_root)
