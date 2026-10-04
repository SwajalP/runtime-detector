from __future__ import annotations

import sys
from collections import defaultdict


class CallEdgeTracer:
    def __init__(self, root: str) -> None:
        self.root = root
        self.edges: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)
        self._stack: list[tuple[str, int]] = []

    def __call__(self, frame, event, arg):
        filename = frame.f_code.co_filename
        if self.root not in filename:
            return self
        if event == "call":
            loc = (filename, frame.f_lineno)
            if self._stack:
                self.edges[self._stack[-1]].add(loc)
            self._stack.append(loc)
        elif event == "return" and self._stack:
            self._stack.pop()
        return self

    def install(self) -> None:
        sys.setprofile(self)

    def uninstall(self) -> None:
        sys.setprofile(None)
