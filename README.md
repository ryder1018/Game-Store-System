# HW3 Game Store System

整合「開發者平台 + 商城 + 遊戲大廳」，全程選單式（輸入數字即可），Demo 無需額外 CLI 參數。

## 1. 啟動服務

開兩個終端視窗：

- Store / Developer Server
  ```bash
  cd hw3
  python3 store_server.py --host 0.0.0.0 --port 17080 --storage_root uploaded_games --db store_db.json
  ```
- Lobby Server
  ```bash
  cd hw3
  python3 lobby_server.py --host 0.0.0.0 --port 18080 \
    --store_host 127.0.0.1 --store_port 17080 \
    --db lobby_db.json --game_host 127.0.0.1 --game_port_start 19100
  ```

重置環境（清空 DB、下載、上架檔案）：`rm -rf uploaded_games store_db.json lobby_db.json downloads`

## 2. Demo 懶人包（依序開視窗）

1) **Store**：照上啟動  
2) **Lobby**：照上啟動  
3) **開發者**（終端 C）
   ```bash
   cd hw3
   python3 developer_client.py --host 127.0.0.1 --port 17080
   ```
   選單：`1 註冊` → `2 登入` → `2 上架新遊戲`，路徑 `tetris_battle`，版本 `v1.0.0`。
4) **玩家 A**（終端 D）
   ```bash
   cd hw3
   python3 lobby_client.py --lobby_host 127.0.0.1 --lobby_port 18080 --store_host 127.0.0.1 --store_port 17080
   ```
   選單：`1 註冊/登入 alice` → `3 下載 Tetris Battle` → `4 建房 room1 並啟動`。
5) **玩家 B**（終端 E）
   ```bash
   cd hw3
   python3 lobby_client.py --lobby_host 127.0.0.1 --lobby_port 18080 --store_host 127.0.0.1 --store_port 17080
   ```
   選單：`1 註冊/登入 bob` → `4 加入 room1`，等待房主啟動後自動進入遊戲。對局結束可在主選單 `5` 評分留言。

## 3. 開發者操作 (D1/D2/D3)

- 進入客戶端：`python3 developer_client.py --host 127.0.0.1 --port 17080`
- 主選單功能：
  - `1 查看我的遊戲`：顯示自己所有版本與上下架狀態（含版本清單）。
  - `2 上架新遊戲`：填寫名稱、版本、簡介、類型、最大玩家數，並上傳資料夾 zip。會驗證 `game_config.json` 以及 server/client entry 是否存在，不合格直接拒絕。
  - `3 更新既有遊戲版本 (D2)`：先列出自己的遊戲並選擇要更新的 game_id，再指定新版本與檔案路徑。若版本號重複或非自己作品會被拒絕。
  - `4 下架遊戲`：只能對自己作品操作。
- 同帳號多處登入：新的登入會踢掉舊 session（舊連線收到 `SESSION_EXPIRED`）。
- 內建遊戲素材：`tetris_battle/`（雙人 pygame）、`gui_number_battle/`（tkinter GUI）、`multi_number_battle/`（3-4 人 CLI）。
- 模板：`template_game/`，可複製為新專案：
  ```bash
  python3 create_game_template.py my_game
  ```
- Demo 順序建議：
  1. 註冊 → 登入。
  2. `2 上架新遊戲`：上傳 `tetris_battle` `v1.0.0`（D1）。
  3. `3 更新既有遊戲版本`：選 `tetris-battle`，輸入新版本（預設會自動提示比最新版高一階的版本號），再次上傳資料夾（D2）。
  4. 查看 `我的遊戲` 確認最新版本。
  5. `4 下架遊戲`（D3），可看到商城列表隱藏。

## 4. 玩家操作 (P1~P4)

- 進入客戶端：`python3 lobby_client.py --lobby_host 127.0.0.1 --lobby_port 18080 --store_host 127.0.0.1 --store_port 17080`
- 主選單功能：
  - `2 商城/遊戲列表`：P1，查看詳細資訊與評價。
  - `3 下載或更新`：P2，放到 `downloads/<Player>/<game>/<version>/`，同時回報 store。
  - `4 房間/遊戲`：建立或加入房間，房主可啟動遊戲（P3）。
  - `5 評分/留言`：下載過的遊戲可送 1–5 分與短評（P4）。
- 大廳畫面會同時顯示線上玩家、房間列表、上架遊戲，助教只需跟著選單數字即可。

### 房間 + 遊戲啟動示範（P3）

1. 玩家 A：`3 下載 Tetris Battle` → `4 建房 room1`（綁定最新版本）。  
2. 玩家 B：`4 加入 room1`。  
3. 房主啟動：Lobby 取最新版本 launch info → 檢查玩家版本 → 啟動 game server，雙端自動啟動 game client。  
4. 結束 → 回到房間/大廳，可在 `5` 評分留言。

## 5. 闖關 B：GUI 雙人對戰（Tetris）

- 主要展示：`tetris_battle/`（type=gui, max_players=2，pygame 介面，方向鍵左右/下、↑旋轉、Space 硬降、C 暫存）。
- 上架步驟：`2 上架新遊戲`，路徑 `tetris_battle`，版本 `v1.0.0`。
- 依賴：`pygame`（若缺少會提示，請安裝 `pip install pygame` 或 `sudo apt-get install python3-pygame`）。
- 玩家 Demo：兩位玩家下載後建立/加入房間，房主啟動即自動開啟 pygame 視窗完成對戰。
- 備用 GUI：`gui_number_battle/`（tkinter，需 `sudo apt-get install python3-tk`），缺少時客戶端會提示並允許離房。

## 6. 闖關 C：3 人同局對戰

- 新增遊戲資料夾：`multi_number_battle/`（改為 Dice Race Party，type=multi，max_players=4，至少 2 人即可開始，建議 3 人以上體驗）。
- 上架步驟：開發者用 `2 上架新遊戲`，路徑 `multi_number_battle`，版本 `v1.0.0`。
- 玩家 Demo（3 個終端玩家 a/b/c）：
  1. 三人登入 lobby_client。
  2. 主選單 `3` 下載 Dice Race Party。
  3. 房主在 `4` 建房（系統會等 3 人到齊），其他人輸入房名加入。
  4. 房主啟動後，輪流按 Enter 擲骰，先達 15 分者勝。
  5. 結束回大廳可評分。

## 7. 遊戲規格

- 每個遊戲資料夾需含 `game_config.json`，內有 `server_entry` / `client_entry`。  
- 內建範例：
  - `tetris_battle`：雙人 pygame 俄羅斯方塊。
  - `gui_number_battle`：雙人 GUI 猜數字（tkinter）。
  - `multi_number_battle`：2-4 人 Dice Race Party (CLI 擲骰)。
  - `template_game`：基礎模板，可自行改寫。

## 8. 資料與目錄

- `uploaded_games/`：開發者上傳後的正式來源，Lobby 也在此啟動 game server。
- `downloads/<Player>/`：玩家獨立下載目錄；`manifest.json` 記錄版本。
- `store_db.json` / `lobby_db.json`：伺服端持久化，重啟不遺失。

## 9. 封包/通訊

- 長度前綴 JSON（`common/framing.py`）。開發者/玩家端只需使用選單，無需額外 shell 指令。

## 10. 已知限制

- Plugin（房間聊天等）未實作。
- 評分以「下載過」為門檻，尚未綁定實際對戰紀錄。
