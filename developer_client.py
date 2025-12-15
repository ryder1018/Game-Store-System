#!/usr/bin/env python3
"""Menu-driven developer client for HW3 store server."""
import argparse
import base64
import json
import os
import shutil
import socket
import tempfile

from common.framing import recv_json, send_json


def slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "game"


def prompt_int(prompt: str, default: int) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("請輸入數字")


def prompt_game_type(prompt: str, default: str) -> str:
    valid = {"cli", "gui", "multi"}
    while True:
        raw = input(prompt).strip().lower()
        if not raw:
            return default
        if raw in valid:
            return raw
        print("只接受 cli / gui / multi")


def bump_version(latest: str | None) -> str:
    latest = latest or ""
    if latest.startswith("v"):
        nums = latest[1:].split(".")
        if all(part.isdigit() for part in nums):
            nums[-1] = str(int(nums[-1]) + 1)
            return "v" + ".".join(nums)
    return "v1.0.1" if not latest else f"{latest}-new"


class StoreConn:
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


def read_config(game_dir: str) -> tuple[dict, str | None]:
    cfg_path = os.path.join(game_dir, "game_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f), None
        except Exception as exc:
            return {}, f"game_config.json 讀取失敗: {exc}"
    return {}, "找不到 game_config.json"


def validate_local_bundle(path: str, cfg: dict) -> tuple[bool, str]:
    if not cfg:
        return False, "缺少 game_config.json，請先依模板建立設定檔。"
    missing_fields = [k for k in ("server_entry", "client_entry") if k not in cfg]
    if missing_fields:
        return False, f"game_config.json 缺少欄位: {', '.join(missing_fields)}"
    missing_files = []
    for key in ("server_entry", "client_entry"):
        entry = cfg.get(key)
        if entry and not os.path.exists(os.path.join(path, entry)):
            missing_files.append(entry)
    if missing_files:
        return False, f"找不到必要檔案: {', '.join(missing_files)}"
    return True, ""


def choose_game(conn: StoreConn):
    resp = conn.request({"op": "dev_list"})
    if not resp.get("ok"):
        return None, resp
    games = resp.get("games", [])
    if not games:
        print("目前沒有上架的遊戲。")
        return None, resp
    print("\n=== 我的遊戲 ===")
    for idx, g in enumerate(games, 1):
        status = "下架" if g.get("removed") else "上架"
        print(f"{idx}. {g['id']} ({g.get('name')}) 最新版本: {g.get('latestVersion')} [{status}]")
    choice = input("選擇編號 (或輸入 game_id): ").strip()
    target = None
    if choice.isdigit():
        i = int(choice)
        if 1 <= i <= len(games):
            target = games[i - 1]
    if not target:
        for g in games:
            if g.get("id") == choice:
                target = g; break
    if not target:
        print("❌ 無效選擇")
    return target, resp


def upload_game(conn: StoreConn, authed: bool, preset_game: dict | None = None):
    if not authed:
        print("請先登入開發者帳號")
        return authed
    path = input("遊戲資料夾路徑 (預設=./template_game): ").strip() or "template_game"
    if not os.path.isdir(path):
        print("❌ 找不到資料夾")
        return authed
    cfg, cfg_err = read_config(path)
    if cfg_err:
        print(f"❌ {cfg_err}")
        return authed
    ok, msg = validate_local_bundle(path, cfg)
    if not ok:
        print(f"❌ {msg}")
        return authed
    default_name = preset_game.get("name") if preset_game else cfg.get("name", "My Game")
    name = input(f"遊戲名稱 (預設={default_name}): ").strip() or default_name
    if preset_game:
        gid = preset_game.get("id")
        print(f"遊戲代號：{gid} (不可變更)")
    else:
        gid = input(f"遊戲代號 (預設={slugify(name)}): ").strip() or slugify(name)
    latest = (preset_game or {}).get("latestVersion", "")
    suggested_version = bump_version(latest) if preset_game else "v1.0.0"
    while True:
        version = input(f"版本標籤 (最新={latest}，預設={suggested_version}): ").strip() or suggested_version
        if not version:
            print("版本不可為空")
            continue
        existing_versions = [v.get("version") for v in (preset_game or {}).get("versions", [])]
        if version in existing_versions:
            print("此版本已存在，請輸入新版本號")
            continue
        break
    default_desc = (preset_game or {}).get("description") or cfg.get("description", "Demo game")
    desc = input(f"簡介 (預設={default_desc}): ").strip() or default_desc
    game_type = (preset_game or {}).get("gameType") or cfg.get("type", "gui")
    max_players = int((preset_game or {}).get("maxPlayers") or cfg.get("max_players", 2))

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = shutil.make_archive(os.path.join(tmp, "bundle"), "zip", path)
        with open(zip_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    resp = conn.request({
        "op": "dev_upload",
        "game_id": gid,
        "name": name,
        "version": version,
        "description": desc,
        "game_type": game_type,
        "max_players": max_players,
        "archive_b64": b64,
    })
    if resp.get("ok"):
        print(f"✅ 上傳完成：{resp.get('game_id')} 版本 {resp.get('version')}")
    else:
        code = resp.get("code")
        friendly = {
            "CONFIG_MISSING": "缺少 game_config.json",
            "CONFIG_INVALID_JSON": "game_config.json 格式錯誤",
            "CONFIG_FIELDS_MISSING": f"缺少欄位: {resp.get('missing')}",
            "ENTRY_NOT_FOUND": f"找不到必要檔案: {resp.get('missing_files')}",
            "VERSION_EXISTS": "版本已存在，請換另一個版本號。",
            "NOT_OWNER": "無法更新他人遊戲。",
            "SESSION_EXPIRED": "登入已失效，請重新登入。",
            "NO_ARCHIVE": "未附上遊戲檔案。",
            "UNPACK_FAIL": "壓縮檔無法解壓縮。",
            "BAD_FIELD": f"欄位錯誤: {resp.get('field')}",
        }
        print(f"❌ 上傳失敗 [{code}] {friendly.get(code,'')}")
        if code == "SESSION_EXPIRED":
            return False
    return authed


def update_game(conn: StoreConn, authed: bool):
    if not authed:
        print("請先登入開發者帳號")
        return authed
    game, resp = choose_game(conn)
    if not game:
        if resp and resp.get("code") in ("AUTH_REQUIRED", "SESSION_EXPIRED"):
            return False
        return authed
    print(f"準備更新 {game.get('id')} (目前最新 {game.get('latestVersion')})")
    return upload_game(conn, authed, preset_game=game)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=17080)
    args = ap.parse_args()

    conn = StoreConn(args.host, args.port)
    authed = False
    while True:
        if not authed:
            print("\n=== 開發者登入 ===")
            print("1. 註冊")
            print("2. 登入")
            print("0. 離開")
            choice = input("選擇: ").strip()
            if choice == "1":
                u = input("帳號: ").strip()
                p = input("密碼: ").strip()
                resp = conn.request({"op": "dev_register", "user": u, "password": p})
                if resp.get("ok"):
                    print("✅ 註冊成功，請登入")
                else:
                    print(f"❌ 註冊失敗: {resp.get('code')}")
            elif choice == "2":
                u = input("帳號: ").strip()
                p = input("密碼: ").strip()
                resp = conn.request({"op": "dev_login", "user": u, "password": p})
                if resp.get("ok"):
                    authed = True
                    if resp.get("session_replaced"):
                        print("⚠️ 已登出先前同帳號的連線")
                    print("✅ 登入成功")
                else:
                    print("❌ 登入失敗", resp.get("code"))
            elif choice == "0":
                break
            else:
                print("請輸入 0-2")
        else:
            print("\n=== 開發者主選單 ===")
            print("1. 查看我的遊戲")
            print("2. 上架新遊戲")
            print("3. 更新既有遊戲版本")
            print("4. 下架遊戲")
            print("0. 登出並離開")
            choice = input("選擇: ").strip()
            if choice == "1":
                resp = conn.request({"op": "dev_list"})
                if resp.get("ok"):
                    for g in resp.get("games", []):
                        status = "下架" if g.get("removed") else "上架"
                        print(f"- {g['id']} v{g.get('latestVersion')} ({status}) {g.get('name')}")
                else:
                    if resp.get("code") in ("AUTH_REQUIRED", "SESSION_EXPIRED"):
                        print("登入已失效，請重新登入")
                        authed = False
                    else:
                        print(resp)
            elif choice == "2":
                authed = upload_game(conn, authed, None)
            elif choice == "3":
                authed = update_game(conn, authed)
            elif choice == "4":
                gid = input("輸入要下架的遊戲代號: ").strip()
                resp = conn.request({"op": "dev_remove", "game_id": gid})
                if resp.get("ok"):
                    print("✅ 已下架")
                else:
                    if resp.get("code") in ("AUTH_REQUIRED", "SESSION_EXPIRED"):
                        print("登入已失效，請重新登入")
                        authed = False
                    else:
                        print(resp)
            elif choice == "0":
                authed = False
                break
            else:
                print("請輸入 0-4")
    conn.close()


if __name__ == "__main__":
    main()
