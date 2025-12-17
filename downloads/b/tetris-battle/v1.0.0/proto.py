"""Shared constants/helpers for Tetris Battle."""
from itertools import groupby

BOARD_W, BOARD_H = 10, 20


def rle_encode_rowmajor(grid):
    flat = []
    for y in range(len(grid)):
        for x in range(len(grid[0])):
            flat.append(grid[y][x])
    out = []
    for val, group in groupby(flat):
        cnt = sum(1 for _ in group)
        out.append(f"{val}:{cnt}")
    return ",".join(out)


def rle_decode_rowmajor(s, w, h):
    nums = []
    if s:
        for part in s.split(","):
            v, c = part.split(":")
            nums.extend([int(v)] * int(c))
    if len(nums) != w * h:
        nums = [0] * (w * h)
    grid = [[0]*w for _ in range(h)]
    it = iter(nums)
    for y in range(h):
        for x in range(w):
            grid[y][x] = next(it)
    return grid
