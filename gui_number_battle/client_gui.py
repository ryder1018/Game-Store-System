#!/usr/bin/env python3
"""Number Battle GUI client (tkinter)."""
import argparse
import json
import queue
import socket
import threading
import tkinter as tk
from tkinter import ttk


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


class GuiClient:
    def __init__(self, host: str, port: int, user: str):
        self.host = host
        self.port = port
        self.user = user
        self.sock = socket.create_connection((host, port))
        send_json(self.sock, {"op": "hello", "user": user})

        self.in_q: queue.Queue[dict] = queue.Queue()
        self.running = True
        self.turn_active = False

        self.root = tk.Tk()
        self.root.title(f"Number Battle GUI - {user}")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status = tk.StringVar(value="等待遊戲開始...")
        ttk.Label(self.root, textvariable=self.status, font=("Arial", 12, "bold")).pack(pady=6)

        self.log = tk.Text(self.root, width=50, height=12, state="disabled")
        self.log.pack(padx=8, pady=4)

        form = ttk.Frame(self.root)
        form.pack(pady=6)
        ttk.Label(form, text="猜 1-50 整數").grid(row=0, column=0, padx=4)
        self.entry = ttk.Entry(form, width=8)
        self.entry.grid(row=0, column=1, padx=4)
        self.send_btn = ttk.Button(form, text="送出", command=self.send_guess, state="disabled")
        self.send_btn.grid(row=0, column=2, padx=4)

        threading.Thread(target=self.net_loop, daemon=True).start()
        self.root.after(100, self.process_queue)

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def net_loop(self):
        try:
            while self.running:
                msg = recv_json(self.sock)
                if not msg:
                    break
                self.in_q.put(msg)
        finally:
            self.running = False
            try:
                self.sock.close()
            except OSError:
                pass

    def process_queue(self):
        while not self.in_q.empty():
            msg = self.in_q.get()
            op = msg.get("op")
            if op == "msg":
                self.append_log(msg.get("text", ""))
            elif op == "your_turn":
                self.status.set("你的回合！請輸入數字")
                self.turn_active = True
                self.send_btn.config(state="normal")
                self.entry.config(state="normal")
                self.entry.delete(0, "end")
                self.entry.focus_set()
            elif op == "end":
                self.status.set("遊戲結束")
                self.turn_active = False
                self.send_btn.config(state="disabled")
                self.entry.config(state="disabled")
        if self.running:
            self.root.after(100, self.process_queue)
        else:
            self.status.set("連線結束")

    def send_guess(self):
        if not self.turn_active:
            return
        try:
            guess = int(self.entry.get().strip())
        except ValueError:
            self.status.set("請輸入數字")
            return
        send_json(self.sock, {"op": "guess", "guess": guess})
        self.turn_active = False
        self.send_btn.config(state="disabled")
        self.entry.config(state="disabled")
        self.status.set("已送出，等待結果...")

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=19100)
    ap.add_argument("--user", default="player")
    args = ap.parse_args()
    client = GuiClient(args.host, args.port, args.user)
    client.run()


if __name__ == "__main__":
    main()
