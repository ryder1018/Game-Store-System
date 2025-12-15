#!/usr/bin/env python3
"""Create a new game folder from the bundled template."""
import argparse
import os
import shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", help="destination folder for new game")
    args = ap.parse_args()
    src = os.path.join(os.path.dirname(__file__), "template_game")
    if not os.path.isdir(src):
        print("template_game not found")
        return
    if os.path.exists(args.dest):
        print("destination already exists")
        return
    shutil.copytree(src, args.dest)
    print(f"Created game folder at {args.dest}. Edit game_config.json and code before上傳。")


if __name__ == "__main__":
    main()
