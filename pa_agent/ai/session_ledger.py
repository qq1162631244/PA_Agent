"""Session-level token usage ledger (no pricing)."""
from __future__ import annotations

import logging

from pa_agent.ai.deepseek_client import AIUsage

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False
    QObject = object  # type: ignore[assignment,misc]
    pyqtSignal = None  # type: ignore[assignment]


class SessionTokenLedger(QObject):  # type: ignore[misc]
    """Accumulates token usage across API calls in a session.

    Signals (Qt only)
    -----------------
    threshold_crossed(str, dict)
        Emitted when context usage crosses warn_pct or 95%.
    updated(dict)
        Emitted after every add() with the current totals dict.
    """

    if _QT_AVAILABLE:
        threshold_crossed = pyqtSignal(str, dict)
        updated = pyqtSignal(dict)

    def __init__(
        self,
        context_window: int = 1_000_000,
        warn_pct: float = 80.0,
        parent: "QObject | None" = None,
    ) -> None:
        super().__init__(parent)
        self._context_window = context_window
        self._warn_pct = warn_pct
        self._yellow_fired = False
        self._red_fired = False

        self.total_input: int = 0
        self.total_cached_input: int = 0
        self.total_output: int = 0

    @property
    def context_used(self) -> int:
        return self.total_input + self.total_output

    def add(self, usage: AIUsage) -> None:
        """Accumulate usage from one API call and emit signals."""
        self.total_input += usage.prompt_tokens
        self.total_cached_input += usage.cached_prompt_tokens
        self.total_output += usage.completion_tokens

        pct = self.context_used / self._context_window * 100.0

        totals = self.breakdown()
        if _QT_AVAILABLE:
            self.updated.emit(totals)

        if pct >= 95.0 and not self._red_fired:
            self._red_fired = True
            logger.warning("Context usage >= 95%% (%.1f%%)", pct)
            if _QT_AVAILABLE:
                self.threshold_crossed.emit("red", totals)
        elif pct >= self._warn_pct and not self._yellow_fired:
            self._yellow_fired = True
            logger.warning("Context usage >= %.0f%% (%.1f%%)", self._warn_pct, pct)
            if _QT_AVAILABLE:
                self.threshold_crossed.emit("yellow", totals)

    def reset(self) -> None:
        """Reset all counters (e.g. on symbol/timeframe switch)."""
        self.total_input = 0
        self.total_cached_input = 0
        self.total_output = 0
        self._yellow_fired = False
        self._red_fired = False

    def breakdown(self) -> dict:
        """Return current totals as a dict for UI display."""
        pct = self.context_used / self._context_window * 100.0
        return {
            "total_input": self.total_input,
            "total_cached_input": self.total_cached_input,
            "total_output": self.total_output,
            "context_used": self.context_used,
            "context_window": self._context_window,
            "context_pct": round(pct, 2),
        }
