from __future__ import annotations

import logging
import random
import time
from typing import List, Tuple

import numpy as np

from controller.data import PixelDisplay, dimensions
from controller.timing import spawn_daemon

GRID_WIDTH = 10
GRID_HEIGHT = 20
BLOCK_SIZE = 3

COLORS = {
    0: (0, 0, 0),
    1: (255, 100, 100),
    2: (100, 255, 100),
    3: (100, 100, 255),
    4: (255, 255, 100),
    5: (255, 100, 255),
    6: (100, 255, 255),
    7: (255, 150, 0),
}

TETRIS_PIECES = [
    [[1, 1, 1, 1]],
    [[1, 1], [1, 1]],
    [[0, 1, 0], [1, 1, 1]],
    [[1, 0, 0], [1, 1, 1]],
    [[0, 0, 1], [1, 1, 1]],
    [[0, 1, 1], [1, 1, 0]],
    [[1, 1, 0], [0, 1, 1]],
]

logger = logging.getLogger(__name__)


class Tetris:
    def __init__(self):
        self.grid = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.int32)
        self.current_piece = [[1, 1, 1, 1]]
        self.current_color = 1
        self.current_x = 0
        self.current_y = 0
        self.score = 0
        self.fall_counter = 0
        self.fall_speed = 0.5
        self.game_over = False
        self._pixels = self._render_grid()

    @property
    def pixels(self) -> PixelDisplay:
        return self._pixels

    def start(self):
        spawn_daemon(self._main_loop)

    def _main_loop(self):
        self._spawn_piece()
        while True:
            self._step()
            self._pixels = self._render_grid()
            time.sleep(0.03)

    def _step(self) -> None:
        self.fall_counter += 0.03
        if self.fall_counter >= self.fall_speed:
            self.fall_counter = 0.0
            if not self._move_down():
                self._place_piece()
                self._check_lines()
                self._spawn_piece()

    def _spawn_piece(self) -> None:
        self.current_piece = random.choice(TETRIS_PIECES)
        self.current_color = random.randint(1, 7)
        self.current_x = GRID_WIDTH // 2 - len(self.current_piece[0]) // 2
        self.current_y = 0

        if self._collides():
            self.game_over = True
            logger.info(f"Game Over! Score: {self.score}")
            time.sleep(2)
            self._reset_game()

    def _reset_game(self) -> None:
        self.grid = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.int32)
        self.score = 0
        self.game_over = False
        self.fall_speed = max(0.1, 0.5 - self.score * 0.01)

    def _collides(self, dx: int = 0, dy: int = 0) -> bool:
        for y, row in enumerate(self.current_piece):
            for x, cell in enumerate(row):
                if cell:
                    grid_x = self.current_x + x + dx
                    grid_y = self.current_y + y + dy

                    if (grid_x < 0 or grid_x >= GRID_WIDTH or
                        grid_y < 0 or grid_y >= GRID_HEIGHT):
                        return True

                    if grid_y >= 0 and self.grid[grid_y, grid_x] != 0:
                        return True
        return False

    def _move_down(self) -> bool:
        if not self._collides(dy=1):
            self.current_y += 1
            return True
        return False

    def _move_horizontal(self, direction: int) -> bool:
        if not self._collides(dx=direction):
            self.current_x += direction
            return True
        return False

    def _place_piece(self) -> None:
        for y, row in enumerate(self.current_piece):
            for x, cell in enumerate(row):
                if cell:
                    grid_x = self.current_x + x
                    grid_y = self.current_y + y
                    if 0 <= grid_y < GRID_HEIGHT and 0 <= grid_x < GRID_WIDTH:
                        self.grid[grid_y, grid_x] = self.current_color

    def _check_lines(self) -> None:
        lines_to_clear = []
        for y in range(GRID_HEIGHT):
            if np.all(self.grid[y] != 0):
                lines_to_clear.append(y)

        if lines_to_clear:
            self.score += len(lines_to_clear)
            self.fall_speed = max(0.1, 0.5 - self.score * 0.01)

            for y in reversed(lines_to_clear):
                self.grid = np.vstack([
                    np.zeros((1, GRID_WIDTH), dtype=np.int32),
                    self.grid[:y]
                ])

    def _find_best_move(self) -> int:
        left_count = 0
        right_count = 0

        test_x = self.current_x - 1
        while not self._collides(dx=test_x - self.current_x):
            left_count += 1
            test_x -= 1

        test_x = self.current_x + 1
        while not self._collides(dx=test_x - self.current_x):
            right_count += 1
            test_x += 1

        if left_count > 0 and random.random() > 0.7:
            return -1
        elif right_count > 0 and random.random() > 0.7:
            return 1
        return 0

    def _step(self) -> None:
        self.fall_counter += 0.03

        if random.random() > 0.85:
            direction = self._find_best_move()
            self._move_horizontal(direction)

        if self.fall_counter >= self.fall_speed:
            self.fall_counter = 0.0
            if not self._move_down():
                self._place_piece()
                self._check_lines()
                self._spawn_piece()

    def _render_grid(self) -> PixelDisplay:
        pixels = np.zeros((dimensions.height, dimensions.width, 3), dtype=np.int32)

        x_offset = max(0, (dimensions.width - GRID_WIDTH * BLOCK_SIZE) // 2)
        y_offset = max(0, (dimensions.height - GRID_HEIGHT * BLOCK_SIZE) // 2)

        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                px = x_offset + x * BLOCK_SIZE
                py = y_offset + y * BLOCK_SIZE

                if px >= 0 and py >= 0 and px + BLOCK_SIZE <= dimensions.width and py + BLOCK_SIZE <= dimensions.height:
                    color_idx = self.grid[y, x]
                    color = COLORS[color_idx]
                    pixels[py:py+BLOCK_SIZE, px:px+BLOCK_SIZE] = color

        for y, row in enumerate(self.current_piece):
            for x, cell in enumerate(row):
                if cell:
                    grid_x = self.current_x + x
                    grid_y = self.current_y + y

                    if 0 <= grid_y < GRID_HEIGHT and 0 <= grid_x < GRID_WIDTH:
                        px = x_offset + grid_x * BLOCK_SIZE
                        py = y_offset + grid_y * BLOCK_SIZE

                        if px >= 0 and py >= 0 and px + BLOCK_SIZE <= dimensions.width and py + BLOCK_SIZE <= dimensions.height:
                            color = COLORS[self.current_color]
                            pixels[py:py+BLOCK_SIZE, px:px+BLOCK_SIZE] = color

        return pixels
