from __future__ import annotations

import logging
import math
import random
import time
from typing import List, Optional, Tuple

import numpy as np

from controller.data import PixelDisplay, dimensions
from controller.timing import spawn_daemon

logger = logging.getLogger(__name__)

BLOCK_SIZE = 4
# Sized so the board exactly fills the display - no dead margin, and the
# piece never falls past what's actually visible.
GRID_WIDTH = dimensions.width // BLOCK_SIZE
GRID_HEIGHT = dimensions.height // BLOCK_SIZE

PIECE_SHAPES = {
    "I": [[1, 1, 1, 1]],
    "O": [[1, 1], [1, 1]],
    "T": [[0, 1, 0], [1, 1, 1]],
    "J": [[1, 0, 0], [1, 1, 1]],
    "L": [[0, 0, 1], [1, 1, 1]],
    "S": [[0, 1, 1], [1, 1, 0]],
    "Z": [[1, 1, 0], [0, 1, 1]],
}
PIECE_NAMES = list(PIECE_SHAPES)

PIECE_COLORS = {
    "I": (80, 200, 235),
    "O": (245, 210, 60),
    "T": (185, 90, 220),
    "J": (75, 110, 235),
    "L": (240, 150, 55),
    "S": (95, 210, 100),
    "Z": (230, 75, 75),
}


def _rotate_cw(shape: List[List[int]]) -> List[List[int]]:
    return [list(row) for row in zip(*shape[::-1])]


def _all_rotations(shape: List[List[int]]) -> List[List[List[int]]]:
    """All distinct rotation states for `shape` (1 for O, 2 for I/S/Z, 4 for
    T/J/L)."""
    rotations: List[List[List[int]]] = []
    current = shape
    for _ in range(4):
        if current not in rotations:
            rotations.append(current)
        current = _rotate_cw(current)
    return rotations


PIECE_ROTATIONS = {name: _all_rotations(shape) for name, shape in PIECE_SHAPES.items()}

# Autoplay heuristic weights: pick the (rotation, column) landing that clears
# the most lines while keeping the stack low, hole-free, and even.
# Higher weights = more aggressive about that factor.
_W_HEIGHT = 1.5
_W_HOLES = 8.0
_W_BUMPINESS = 1.2
_W_LINES = 10.0

# The piece first slides to its target column (no falling yet), then falls
# straight down once aligned - so it only ever makes exactly the moves it
# needs, instead of wandering.
ALIGN_INTERVAL = 0.05
FALL_INTERVAL_START = 0.14
FALL_INTERVAL_MIN = 0.07
FALL_INTERVAL_STEP = 0.004  # speeds up slightly with each line cleared

TICK = 0.03
LINE_FLASH_SECONDS = 0.12
GAME_OVER_FLASH_SECONDS = 0.2
GAME_OVER_PAUSE = 1.0


class _Piece:
    __slots__ = ("name", "rotation", "x", "y")

    def __init__(self, name: str, rotation: int, x: int, y: int):
        self.name = name
        self.rotation = rotation
        self.x = x
        self.y = y

    @property
    def shape(self) -> List[List[int]]:
        return PIECE_ROTATIONS[self.name][self.rotation]


def _collides(shape: List[List[int]], x: int, y: int, grid: np.ndarray) -> bool:
    for row_i, row in enumerate(shape):
        for col_i, cell in enumerate(row):
            if not cell:
                continue
            grid_x, grid_y = x + col_i, y + row_i
            if grid_x < 0 or grid_x >= GRID_WIDTH or grid_y >= GRID_HEIGHT:
                return True
            if grid_y >= 0 and grid[grid_y, grid_x]:
                return True
    return False


def _drop_row(shape: List[List[int]], x: int, grid: np.ndarray) -> Optional[int]:
    """The lowest non-colliding row for `shape` at column `x`, or None if it
    can't even be placed at the top."""
    if _collides(shape, x, 0, grid):
        return None
    y = 0
    while not _collides(shape, x, y + 1, grid):
        y += 1
    return y


def _with_piece_placed(
    grid: np.ndarray, shape: List[List[int]], x: int, y: int, color_id: int
) -> np.ndarray:
    trial = grid.copy()
    for row_i, row in enumerate(shape):
        for col_i, cell in enumerate(row):
            if cell:
                trial[y + row_i, x + col_i] = color_id
    return trial


def _board_metrics(grid: np.ndarray) -> Tuple[int, int, int]:
    """(aggregate column height, hole count, bumpiness) for `grid`."""
    heights = []
    holes = 0
    for col in range(GRID_WIDTH):
        column = grid[:, col]
        filled = np.nonzero(column)[0]
        if len(filled) == 0:
            heights.append(0)
            continue
        top = filled[0]
        heights.append(GRID_HEIGHT - top)
        holes += int(np.sum(column[top:] == 0))
    bumpiness = sum(abs(heights[i] - heights[i + 1]) for i in range(len(heights) - 1))
    return sum(heights), holes, bumpiness


def _count_full_lines(grid: np.ndarray) -> int:
    return int(np.sum(np.all(grid != 0, axis=1)))


def _clear_lines(grid: np.ndarray) -> Tuple[np.ndarray, int]:
    keep_rows = [row for row in grid if not np.all(row != 0)]
    cleared = GRID_HEIGHT - len(keep_rows)
    if cleared == 0:
        return grid, 0
    new_grid = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.int32)
    if keep_rows:
        new_grid[cleared:] = np.array(keep_rows)
    return new_grid, cleared


class Tetris:
    def __init__(self):
        self.grid = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.int32)
        self.score = 0
        self.fall_interval = FALL_INTERVAL_START

        self.piece: Optional[_Piece] = None
        self.target_x = 0
        self.phase = "aligning"  # "aligning" | "falling"
        self._align_counter = 0.0
        self._fall_counter = 0.0

        self._pixels = np.zeros(
            (dimensions.height, dimensions.width, 3), dtype=np.int32
        )

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self) -> None:
        spawn_daemon(self._main_loop)

    def _main_loop(self) -> None:
        self._spawn_piece()
        while True:
            self._step()
            self._pixels = self._render()
            time.sleep(TICK)

    def _step(self) -> None:
        if self.phase == "aligning":
            self._align_counter += TICK
            if self._align_counter >= ALIGN_INTERVAL:
                self._align_counter = 0.0
                self._advance_alignment()
        else:
            self._fall_counter += TICK
            if self._fall_counter >= self.fall_interval:
                self._fall_counter = 0.0
                self._advance_fall()

    def _advance_alignment(self) -> None:
        assert self.piece is not None
        piece = self.piece
        if piece.x == self.target_x:
            self.phase = "falling"
            return

        step = 1 if piece.x < self.target_x else -1
        new_x = piece.x + step
        if not _collides(piece.shape, new_x, piece.y, self.grid):
            piece.x = new_x
        else:
            # Path unexpectedly blocked (a tall neighboring stack) - stop
            # sliding and just fall from here rather than getting stuck.
            self.phase = "falling"

    def _advance_fall(self) -> None:
        assert self.piece is not None
        piece = self.piece
        if not _collides(piece.shape, piece.x, piece.y + 1, self.grid):
            piece.y += 1
            return

        color_id = PIECE_NAMES.index(piece.name) + 1
        self.grid = _with_piece_placed(
            self.grid, piece.shape, piece.x, piece.y, color_id
        )

        full_rows = [y for y in range(GRID_HEIGHT) if np.all(self.grid[y] != 0)]
        if full_rows:
            self._flash_lines(full_rows)

        self.grid, cleared = _clear_lines(self.grid)
        if cleared:
            self.score += cleared
            self.fall_interval = max(
                FALL_INTERVAL_MIN, self.fall_interval - FALL_INTERVAL_STEP * cleared
            )

        self._spawn_piece()

    def _best_placement(self, name: str) -> Optional[Tuple[int, int, int]]:
        """The (rotation, column, landing row) that scores best by the
        standard height/holes/bumpiness/lines heuristic."""
        color_id = PIECE_NAMES.index(name) + 1
        best = None
        best_score = -math.inf

        for rotation, shape in enumerate(PIECE_ROTATIONS[name]):
            width = len(shape[0])
            for x in range(GRID_WIDTH - width + 1):
                landing_y = _drop_row(shape, x, self.grid)
                if landing_y is None:
                    continue

                trial = _with_piece_placed(self.grid, shape, x, landing_y, color_id)
                lines_cleared = _count_full_lines(trial)
                if lines_cleared:
                    trial, _ = _clear_lines(trial)
                agg_height, holes, bumpiness = _board_metrics(trial)

                score = (
                    _W_LINES * lines_cleared
                    - _W_HEIGHT * agg_height
                    - _W_HOLES * holes
                    - _W_BUMPINESS * bumpiness
                )
                if score > best_score:
                    best_score = score
                    best = (rotation, x, landing_y)

        return best

    def _spawn_piece(self) -> None:
        name = random.choice(PIECE_NAMES)
        placement = self._best_placement(name)
        rotation = placement[0] if placement else 0
        shape = PIECE_ROTATIONS[name][rotation]
        spawn_x = (GRID_WIDTH - len(shape[0])) // 2

        if placement is None or _collides(shape, spawn_x, 0, self.grid):
            logger.info(f"Tetris game over, score={self.score}")
            self._show_game_over()
            self.grid = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.int32)
            self.score = 0
            self.fall_interval = FALL_INTERVAL_START
            self._spawn_piece()
            return

        _, target_x, _ = placement
        self.piece = _Piece(name=name, rotation=rotation, x=spawn_x, y=0)
        self.target_x = target_x
        self.phase = "aligning"
        self._align_counter = 0.0
        self._fall_counter = 0.0

    def _flash_lines(self, rows: List[int]) -> None:
        base = self._render_grid_only()
        y0, y1 = min(rows) * BLOCK_SIZE, (max(rows) + 1) * BLOCK_SIZE
        flashed = base.copy()
        flashed[y0:y1, :] = (255, 255, 255)
        for _ in range(2):
            self._pixels = flashed
            time.sleep(LINE_FLASH_SECONDS)
            self._pixels = base
            time.sleep(LINE_FLASH_SECONDS)

    def _show_game_over(self) -> None:
        """Fill one column to the top to make game-over obvious, then flash red."""
        full_column_idx = GRID_WIDTH // 2
        self.grid[:, full_column_idx] = 7

        base = self._render_grid_only()
        filled = base.any(axis=2, keepdims=True)
        flashed = np.where(filled, np.array((255, 60, 60)), base).astype(np.int32)
        for _ in range(3):
            self._pixels = flashed
            time.sleep(GAME_OVER_FLASH_SECONDS)
            self._pixels = base
            time.sleep(GAME_OVER_FLASH_SECONDS)
        time.sleep(GAME_OVER_PAUSE)

    def _render(self) -> PixelDisplay:
        pixels = self._render_grid_only()
        if self.piece is not None:
            self._draw_piece(pixels, self.piece)
        return pixels

    def _render_grid_only(self) -> PixelDisplay:
        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)
        filled_cells = np.transpose(np.nonzero(self.grid))
        for grid_y, grid_x in filled_cells:
            color = PIECE_COLORS[PIECE_NAMES[self.grid[grid_y, grid_x] - 1]]
            self._draw_block(pixels, grid_x, grid_y, color)
        return pixels

    def _draw_piece(self, pixels: PixelDisplay, piece: _Piece) -> None:
        color = PIECE_COLORS[piece.name]
        for row_i, row in enumerate(piece.shape):
            for col_i, cell in enumerate(row):
                if cell:
                    self._draw_block(pixels, piece.x + col_i, piece.y + row_i, color)

    @staticmethod
    def _draw_block(pixels: PixelDisplay, grid_x: int, grid_y: int, color) -> None:
        """Fill one cell with solid color."""
        px, py = grid_x * BLOCK_SIZE, grid_y * BLOCK_SIZE
        pixels[py : py + BLOCK_SIZE, px : px + BLOCK_SIZE] = color
