"""Límite de intentos fallidos de inicio de sesión (contra ataques de fuerza bruta).

Se guarda en memoria: es suficiente con un solo servidor (Render, plan gratuito) y se reinicia si
el servidor se reinicia.
"""

import math
import threading
import time
from collections import defaultdict, deque

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class LoginLimiter:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        # Deja solo los fallos dentro de la ventana
        failures = self._failures[key]
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        return failures

    def retry_after(self, key: tuple[str, str]) -> int:
        """Segundos que faltan para poder intentar de nuevo; 0 si no está bloqueado."""
        now = time.monotonic()
        with self._lock:
            failures = self._recent(key, now)
            if len(failures) < self.max_failures:
                if not failures:
                    self._failures.pop(key, None)
                return 0
            return math.ceil(failures[-self.max_failures] + self.window - now)

    def record_failure(self, key: tuple[str, str]) -> None:
        now = time.monotonic()
        with self._lock:
            self._recent(key, now).append(now)

    def reset(self, key: tuple[str, str] | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


login_limiter = LoginLimiter()
