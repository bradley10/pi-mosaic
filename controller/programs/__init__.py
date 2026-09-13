from abc import abstractmethod
from typing import Protocol

from controller.data import PixelDisplay


class Program(Protocol):
    @property
    def pixels(self) -> PixelDisplay:
        """Return the program's current frame."""
        ...

    @abstractmethod
    def start(self) -> None:
        """Start the program's background loop(s)."""
        raise NotImplementedError
