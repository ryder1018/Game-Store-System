#!/usr/bin/env python3
"""Menu-driven lobby player client."""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Dict, Optional
import time
import socket
import importlib.util

from common.framing import recv_json, send_json


class LobbyConn:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((host, port))
        recv_json(self.s)  # hello

    def request(self, payload: dict) -> dict:
        send_json(self.s, payload)
        resp = recv_json(self.s)
        return resp or {"ok": False, "code": "NO_RESPONSE"}

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class StoreAPI:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def call(self, payload: dict) -> dict:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            # consume store server hello
            _ = recv_json(s)
            send_json(s, payload)
            resp = recv_json(s)
            return resp or {"ok": False, "code": "NO_RESPONSE"}

    def list_games(self):
        return self.call({"op": "list_games"})

    def game_detail(self, gid: str):
        return self.call({"op": "game_detail", "game_id": gid})

    def download_game(self, gid: str, player: str, version: Optional[str] = None):
        payload = {"op": "download_game", "game_id": gid, "player": player}
        if version:
            payload["version"] = version
        return self.call(payload)

    def record_rating(self, gid: str, player: str, score: int, comment: str):
        return self.call({"op": "record_rating", "game_id": gid, "player": player, "score": score, "comment": comment})


def load_manifest(base_dir: str) -> Dict:
    path = os.path.join(base_dir, "manifest.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"downloads": {}}


def save_manifest(base_dir: str, data: Dict):
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_download(api: StoreAPI, lobby: LobbyConn, user: str, downloads_root: str, gid: str, target_version: Optional[str]) -> Optional[str]:
    """Return local path, downloading if needed."""
    manifest = load_manifest(downloads_root)
    current = manifest["downloads"].get(gid, {})
    if current and (target_version is None or current.get("version") == target_version):
        return current.get("path")

    print(f"⬇️  正在下載 {gid} ...")
    resp = api.download_game(gid, user, target_version)
    if not resp.get("ok"):
        print("下載失敗", resp)
        return None
    version = resp.get("version")
    data_b64 = resp.get("archive_b64", "")
    try:
        blob = base64.b64decode(data_b64.encode("utf-8"))
    except Exception as exc:
        print("解碼失敗", exc)
        return None

    game_dir = os.path.join(downloads_root, gid, version)
    os.makedirs(game_dir, exist_ok=True)
    zip_path = os.path.join(downloads_root, gid, f"{version}.zip")
    with open(zip_path, "wb") as f:
        f.write(blob)
    try:
        shutil.unpack_archive(zip_path, game_dir)
    except Exception as exc:
        print("解壓縮失敗", exc)
        return None

    manifest["downloads"][gid] = {"version": version, "path": game_dir}
    save_manifest(downloads_root, manifest)
    lobby.request({"op": "record_download", "game_id": gid, "version": version})
    print(f"✅ 下載完成 {gid} 版本 {version}")
    return game_dir


def port_alive(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def choose_game(api: StoreAPI) -> Optional[dict]:
    resp = api.list_games()
    if not resp.get("ok"):
        print("讀取遊戲列表失敗")
        return None
    games = resp.get("games", [])
    if not games:
        print("目前沒有上架的遊戲")
        return None
    for idx, g in enumerate(games, 1):
        print(f"{idx}. {g['name']} ({g['id']}) v{g.get('latestVersion')} ⭐ {g.get('ratingAvg')} ({g.get('ratingCount')})")
    sel = input("選擇遊戲編號: ").strip()
    try:
        i = int(sel) - 1
        if 0 <= i < len(games):
            return games[i]
    except ValueError:
        pass
    print("輸入錯誤")
    return None


def show_game_detail(api: StoreAPI):
    g = choose_game(api)
    if not g:
        return
    resp = api.game_detail(g["id"])
    if not resp.get("ok"):
        print(resp); return
    game = resp["game"]
    print(f"\n=== {game.get('name')} ({game.get('id')}) ===")
    print("作者:", game.get("author"))
    print("版本:", game.get("latestVersion"))
    print("人數:", game.get("maxPlayers"))
    print("簡介:", game.get("description"))
    print("評價:", f"{game.get('ratingAvg',0)} / 5, {len(game.get('ratings',[]))} 則")
    for r in game.get("ratings", [])[:5]:
        print(f"- {r.get('user')}: {r.get('score')}⭐ {r.get('comment')}")


def launch_client(gid: str, game_dir: str, server_host: str, server_port: int, user: str) -> tuple[bool, bool]:
    cfg_path = os.path.join(game_dir, "game_config.json")
    client_entry = "client.py"
    game_type = "cli"
    requires = []
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            client_entry = cfg.get("client_entry", client_entry)
            game_type = cfg.get("type", game_type)
            requires = cfg.get("requires", [])
        except Exception:
            pass
    # 必要模組檢查
    for mod in requires:
        if importlib.util.find_spec(mod) is None:
            msg = f"⚠️ 缺少必要模組 {mod}，請先安裝後再啟動"
            if mod == "tkinter":
                msg += "（例如 Ubuntu 可執行: sudo apt-get install python3-tk）"
            elif mod == "pygame":
                msg += "（例如: pip install pygame 或 apt-get install python3-pygame）"
            print(msg)
            return False, True
    cmd = [sys.executable, client_entry, "--host", server_host, "--port", str(server_port), "--user", user]
    env = os.environ.copy()
    env["PYTHONPATH"] = game_dir + os.pathsep + env.get("PYTHONPATH", "")
    print(f"▶️ 啟動遊戲客戶端 {cmd}")
    # Inherit stdio so玩家可以看到提示與互動；僅在非零退出時給出簡短提示。
    res = subprocess.run(cmd, cwd=game_dir, env=env)
    if res.returncode != 0:
        print(f"遊戲啟動失敗：程式退出碼 {res.returncode}")
        return False, False
    return True, False


def play_flow(api: StoreAPI, lobby: LobbyConn, user: str, downloads_root: str):
    # list rooms
    resp = lobby.request({"op": "list_rooms"})
    rooms = resp.get("rooms", []) if resp.get("ok") else []
    print("\n=== 房間列表 ===")
    if not rooms:
        print("(空)")
    for r in rooms:
        print(f"- {r['id']} [{r.get('status')}] {r.get('members')} 遊戲 {r.get('game_id')}@{r.get('game_version')}")
    rid = input("輸入房間代號 (空白則新建): ").strip()
    room = None
    if not rid:
        game = choose_game(api)
        if not game:
            return
        rid = input("輸入新房間名稱: ").strip() or f"room-{user}"
        resp = lobby.request({"op": "create_room", "room": rid, "game_id": game["id"]})
        if not resp.get("ok"):
            print("建立失敗", resp); return
        room = resp.get("room")
        print(f"房間建立完成：{room.get('id')}，等待其他玩家加入。")
    else:
        resp = lobby.request({"op": "join_room", "room": rid})
        if not resp.get("ok"):
            print("加入失敗", resp); return
        room = resp.get("room")

    # 取得最新房間資訊（包含其他已加入成員）
    info = lobby.request({"op": "room_info", "room": rid})
    if info.get("ok"):
        room = info.get("room", room)

    if not room:
        print("無房間資訊"); return
    # ensure download
    game_dir = ensure_download(api, lobby, user, downloads_root, room["game_id"], room.get("game_version"))
    if not game_dir:
        return

    # 進入房間狀態輪詢
    fail_count = 0
    while True:
        info = lobby.request({"op": "room_info", "room": room["id"]})
        if not info.get("ok"):
            print("無法取得房間資訊", info)
            return
        room = info.get("room", room)
        members = room.get("members", [])
        is_host = room.get("host") == user
        print(f"\n房間 {room['id']} 狀態={room.get('status')} 成員={members}")

        # 若已啟動遊戲，直接連線
        # 注意：避免事先用 port_alive 探測，否則會佔用 game server 的連線名額，導致伺服器提早關閉。
        if room.get("status") == "playing" and room.get("server"):
            server = room["server"]
            if server.get("port"):
                ok, fatal = launch_client(room["game_id"], game_dir, server.get("host"), server.get("port"), user)
                if not ok:
                    fail_count += 1
                    if fatal or fail_count >= 2:
                        choice = input("啟動失敗，1)重試 2)離開房間 [2]: ").strip() or "2"
                        if choice == "1":
                            continue
                        lobby.request({"op": "leave_room", "room": room["id"]})
                        print("已離開房間")
                        return
                    print("連線遊戲失敗，等待房間狀態更新...")
                    time.sleep(1)
                    continue
                # 遊戲結束後回到房間等待狀態
                fail_count = 0
                continue
            print("遊戲伺服器資訊缺失，請重試")
            return

        # 主人選項
        if is_host:
            if len(members) < 2:
                choice = input("等待其他玩家加入。1)刷新 2)離開房間 [1]: ").strip() or "1"
                if choice == "2":
                    lobby.request({"op": "leave_room", "room": room["id"]})
                    print("已離開房間")
                    return
                continue
            choice = input("已有人加入，是否啟動？ 1)啟動 2)刷新 3)離開房間 [1]: ").strip() or "1"
            if choice == "1":
                resp = lobby.request({"op": "start_room", "room": room["id"]})
                if resp.get("ok"):
                    server = resp.get("server", {})
                    if server.get("port"):
                        ok, fatal = launch_client(room["game_id"], game_dir, server.get("host"), server.get("port"), user)
                        if not ok:
                            fail_count += 1
                            if fatal or fail_count >= 2:
                                choice = input("啟動失敗，1)重試 2)離開房間 [2]: ").strip() or "2"
                                if choice == "1":
                                    continue
                                lobby.request({"op": "leave_room", "room": room["id"]})
                                print("已離開房間")
                                return
                            print("連線遊戲失敗，等待房間狀態更新...")
                            time.sleep(1)
                            continue
                        # 遊戲結束後回到房間迴圈，保持在房間
                        fail_count = 0
                        continue
                    else:
                        print("伺服器資訊缺失，啟動失敗")
                    continue
                if resp.get("code") == "NEED_TWO_PLAYERS":
                    print("房間人數不足，請再試一次")
                else:
                    print("啟動失敗", resp)
            elif choice == "3":
                lobby.request({"op": "leave_room", "room": room["id"]})
                print("已離開房間")
                return
            # choice == 2: refresh
            time.sleep(1); continue

        # 非主人：等待或退出
        choice = input("房主尚未啟動。1)刷新 2)離開房間 [1]: ").strip() or "1"
        if choice == "2":
            lobby.request({"op": "leave_room", "room": room["id"]})
            print("已離開房間")
            return
        # 預設選擇 1 會自動刷新，避免回到主選單
        continue


def rating_flow(api: StoreAPI, user: str, downloads_root: str):
    manifest = load_manifest(downloads_root)
    downloads = manifest.get("downloads", {})
    if not downloads:
        print("尚未下載任何遊戲，無法評分")
        return
    gids = list(downloads.keys())
    for idx, gid in enumerate(gids, 1):
        print(f"{idx}. {gid} (版本 {downloads[gid]['version']})")
    sel = input("選擇要評分的遊戲: ").strip()
    try:
        i = int(sel) - 1
        if not (0 <= i < len(gids)):
            raise ValueError()
    except ValueError:
        print("輸入錯誤")
        return
    gid = gids[i]
    score = int(input("分數 1-5: ").strip() or "5")
    comment = input("留言 (可空白): ").strip()
    resp = api.record_rating(gid, user, score, comment)
    print(resp)


def show_status(api: StoreAPI, lobby: LobbyConn):
    presp = lobby.request({"op": "list_players"})
    rresp = lobby.request({"op": "list_rooms"})
    gresp = api.list_games()
    print("\n=== 大廳狀態 ===")
    if presp.get("ok"):
        online = [u for u in presp.get("players", []) if u.get("online")]
        print("線上玩家:", [u.get("user") for u in online])
    if rresp.get("ok"):
        rooms = rresp.get("rooms", [])
        print("公開房間:")
        for r in rooms:
            print(f"  {r['id']} [{r.get('status')}] {r.get('members')}")
    if gresp.get("ok"):
        print("上架遊戲:", [g.get("id") for g in gresp.get("games", [])])


def auth_flow(lobby: LobbyConn) -> Optional[str]:
    while True:
        print("\n=== 玩家登入 ===")
        print("1. 註冊")
        print("2. 登入")
        print("0. 離開")
        ch = input("選擇: ").strip()
        if ch == "1":
            u = input("帳號: ").strip()
            p = input("密碼: ").strip()
            resp = lobby.request({"op": "register", "user": u, "password": p})
            print(resp)
        elif ch == "2":
            u = input("帳號: ").strip()
            p = input("密碼: ").strip()
            resp = lobby.request({"op": "login", "user": u, "password": p})
            if resp.get("ok"):
                print("✅ 登入成功")
                return u
            print("❌ 登入失敗")
        elif ch == "0":
            return None
        else:
            print("請輸入 0-2")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lobby_host", default="127.0.0.1")
    ap.add_argument("--lobby_port", type=int, default=18080)
    ap.add_argument("--store_host", default="127.0.0.1")
    ap.add_argument("--store_port", type=int, default=17080)
    args = ap.parse_args()

    lobby = LobbyConn(args.lobby_host, args.lobby_port)
    store = StoreAPI(args.store_host, args.store_port)
    user = auth_flow(lobby)
    if not user:
        return
    downloads_root = os.path.join(os.path.dirname(__file__), "downloads", user)
    while True:
        print("\n=== 玩家主選單 ===")
        print("1. 大廳狀態")
        print("2. 商城瀏覽/詳細資訊")
        print("3. 下載或更新遊戲")
        print("4. 建立/加入房間並啟動遊戲")
        print("5. 評分與評論")
        print("0. 登出並離開")
        ch = input("選擇: ").strip()
        if ch == "1":
            show_status(store, lobby)
        elif ch == "2":
            show_game_detail(store)
        elif ch == "3":
            g = choose_game(store)
            if g:
                ensure_download(store, lobby, user, downloads_root, g["id"], g.get("latestVersion"))
        elif ch == "4":
            play_flow(store, lobby, user, downloads_root)
        elif ch == "5":
            rating_flow(store, user, downloads_root)
        elif ch == "0":
            lobby.request({"op": "logout"})
            break
        else:
            print("請輸入 0-5")
    lobby.close()


if __name__ == "__main__":
    main()
