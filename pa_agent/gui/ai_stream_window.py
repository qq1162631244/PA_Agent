"""Live AI stream panel: token-by-token reasoning display only."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from pa_agent.config.settings import Settings
    from pa_agent.orchestrator.free_chat import FreeChatSession
    from pa_agent.util.threading import CancelToken

logger = logging.getLogger(__name__)

_YELLOW_PCT = 80.0
_RED_PCT = 95.0
_STYLE_NORMAL = ""
_STYLE_YELLOW = "QProgressBar#tokenProgress::chunk { background-color: #e6b800; }"
_STYLE_RED = "QProgressBar#tokenProgress::chunk { background-color: #cc0000; }"


class _ChatWorker(QThread):
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)
    reasoning_token = pyqtSignal(str)
    content_token = pyqtSignal(str)

    def __init__(
        self,
        session: "FreeChatSession",
        user_text: str,
        cancel_token: "CancelToken",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._user_text = user_text
        self._cancel_token = cancel_token

    def run(self) -> None:
        try:
            reply = self._session.send(
                self._user_text,
                self._cancel_token,
                on_reasoning_token=lambda c: self.reasoning_token.emit(c),
                on_content_token=lambda c: self.content_token.emit(c),
            )
            self.finished.emit(reply.content, reply.reasoning_content or "")
        except Exception as exc:  # noqa: BLE001
            logger.error("ChatWorker error: %s", exc, exc_info=True)
            self.error.emit(str(exc))


class AIStreamPanel(QWidget):
    """Live stream viewer: reasoning only, context usage, follow-up input."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Optional["FreeChatSession"] = None
        self._cancel_token: Optional["CancelToken"] = None
        self._worker: Optional[_ChatWorker] = None
        self._sending = False
        self._red_warned = False
        self._settings: Optional["Settings"] = None

        self._stage: str = ""
        self._reasoning_chars = 0
        self._stage_t0 = 0.0
        self._finalized_stages: set[str] = set()

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._phase_label = QLabel("等待分析…")
        self._phase_label.setObjectName("stageHeader")
        layout.addWidget(self._phase_label)

        self._mode_label = QLabel("")
        self._mode_label.setObjectName("mutedLabel")
        layout.addWidget(self._mode_label)

        rl = QLabel("🧠 思考过程")
        rl.setStyleSheet("color: #a371f7; font-weight: bold;")
        layout.addWidget(rl)

        mono = QFont("Cascadia Mono", 11)
        if not mono.exactMatch():
            mono = QFont("Consolas", 11)
        self._reasoning_edit = QPlainTextEdit()
        self._reasoning_edit.setObjectName("reasoningPane")
        self._reasoning_edit.setReadOnly(True)
        self._reasoning_edit.setFont(mono)
        self._reasoning_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self._reasoning_edit, stretch=1)

        self._stats_label = QLabel("思考 0 字")
        self._stats_label.setObjectName("mutedLabel")
        layout.addWidget(self._stats_label)

        layout.addLayout(self._build_token_bar())
        layout.addWidget(self._build_input_area())

        self.set_input_enabled(False)

    def _build_token_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("上下文"))
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("tokenProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setMaximumWidth(320)
        row.addWidget(self._progress_bar)
        self._token_label = QLabel("—")
        self._token_label.setObjectName("mutedLabel")
        row.addWidget(self._token_label, stretch=1)
        return row

    def _build_input_area(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        self._input_edit = QPlainTextEdit()
        self._input_edit.setObjectName("chatInput")
        self._input_edit.setPlaceholderText("分析完成后可继续追问…")
        self._input_edit.setMaximumHeight(80)
        row.addWidget(self._input_edit, stretch=1)
        self._send_btn = QPushButton("发送")
        self._send_btn.setObjectName("primaryButton")
        self._send_btn.setMinimumWidth(72)
        self._send_btn.clicked.connect(self._on_send_or_stop)
        row.addWidget(self._send_btn)
        return box

    def bind_settings(self, settings: Optional["Settings"]) -> None:
        self._settings = settings
        self._refresh_mode_label()

    def _refresh_mode_label(self) -> None:
        if self._settings is None:
            self._mode_label.setText("")
            return
        p = self._settings.provider
        thinking = "enabled" if p.thinking else "disabled"
        self._mode_label.setText(
            f"API: thinking={thinking} · reasoning_effort={p.reasoning_effort} · {p.model}"
        )

    def _update_stats(self) -> None:
        self._stats_label.setText(f"思考 {self._reasoning_chars:,} 字")

    def _append_reasoning(self, chunk: str) -> None:
        if not chunk:
            return
        self._reasoning_chars += len(chunk)
        self._reasoning_edit.moveCursor(QTextCursor.MoveOperation.End)
        self._reasoning_edit.insertPlainText(chunk)
        self._reasoning_edit.moveCursor(QTextCursor.MoveOperation.End)
        self._update_stats()

    def _append_user_message(self, text: str) -> None:
        """Append follow-up user text in red in the reasoning pane."""
        from pa_agent.gui.theme.tokens import ACCENT_DANGER

        cursor = self._reasoning_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        normal = QTextCharFormat()
        user_fmt = QTextCharFormat()
        user_fmt.setForeground(QColor(ACCENT_DANGER))

        cursor.insertText("\n【用户】\n", normal)
        cursor.insertText(text, user_fmt)
        cursor.insertText("\n", normal)
        self._reasoning_edit.setTextCursor(cursor)
        self._reasoning_edit.ensureCursorVisible()

    @staticmethod
    def _stage_title(stage: str) -> str:
        return "阶段一 · 市场诊断" if stage == "stage1" else "阶段二 · 交易决策"

    def _begin_stage(self, stage: str, title: str) -> None:
        if self._stage and self._stage != stage and self._stage not in self._finalized_stages:
            self.finalize_stage(self._stage)
        self._stage = stage
        self._stage_t0 = time.monotonic()
        self._reasoning_chars = 0
        sep = "\n" + "─" * 48 + "\n"
        if self._reasoning_edit.toPlainText():
            self._reasoning_edit.appendPlainText(sep)
        self._phase_label.setText(f"▶ {title} — 思考中…")
        self._update_stats()

    def _end_stage(self, title: str) -> None:
        elapsed = time.monotonic() - self._stage_t0
        self._phase_label.setText(
            f"✓ {title} — 完成 ({elapsed:.1f}s) · 思考 {self._reasoning_chars:,} 字"
        )

    def clear(self) -> None:
        self._reasoning_edit.clear()
        self._reasoning_chars = 0
        self._stage = ""
        self._finalized_stages.clear()
        self._phase_label.setText("等待分析…")
        self._update_stats()
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("0%")
        self._progress_bar.setStyleSheet(_STYLE_NORMAL)
        self._token_label.setText("—")
        self._red_warned = False

    def on_analysis_started(self) -> None:
        self.set_input_enabled(False)
        self._session = None
        self._cancel_token = None
        self.clear()

    def on_record_saved(self) -> None:
        self.set_input_enabled(True)

    def set_input_enabled(self, enabled: bool) -> None:
        self._input_edit.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)

    def set_session(
        self,
        session: "FreeChatSession",
        cancel_token: "CancelToken",
    ) -> None:
        self._session = session
        self._cancel_token = cancel_token

    def update_token_display(self, data: dict) -> None:
        context_used = data.get("context_used", 0)
        context_window = data.get("context_window", 1_000_000)
        total_input = data.get("total_input", 0)
        total_output = data.get("total_output", 0)
        pct = (context_used / context_window * 100.0) if context_window > 0 else 0.0
        pct_int = min(100, int(pct))
        self._progress_bar.setValue(pct_int)
        self._progress_bar.setFormat(f"{pct:.1f}%")
        if pct >= _RED_PCT:
            self._progress_bar.setStyleSheet(_STYLE_RED)
            if not self._red_warned:
                self._red_warned = True
                QMessageBox.warning(
                    self,
                    "上下文用量警告",
                    f"上下文用量已达 {pct:.1f}%，接近上限。",
                )
        elif pct >= _YELLOW_PCT:
            self._progress_bar.setStyleSheet(_STYLE_YELLOW)
        else:
            self._progress_bar.setStyleSheet(_STYLE_NORMAL)
        self._token_label.setText(
            f"{context_used:,} / {context_window:,} · "
            f"in {total_input:,} / out {total_output:,}"
        )

    def on_stage_prompt_ready(self, stage: str, system: str, user: str) -> None:
        del system, user
        self._begin_stage(stage, self._stage_title(stage))

    def on_analysis_progress(self, text: str) -> None:
        """Sync phase header with orchestrator progress events."""
        if text in ("阶段一完成", "阶段一失败"):
            self.finalize_stage("stage1")
        elif text in ("阶段二完成", "阶段二失败"):
            self.finalize_stage("stage2")
        elif text == "已取消" and self._stage:
            self.finalize_stage(self._stage)

    def on_reasoning_token(self, stage: str, chunk: str) -> None:
        if stage != self._stage:
            return
        self._append_reasoning(chunk)

    def on_content_token(self, stage: str, chunk: str) -> None:
        del stage, chunk  # 实时页仅展示思考过程，不显示正式回答

    def finalize_stage(self, stage: str) -> None:
        if stage in self._finalized_stages:
            return
        self._finalized_stages.add(stage)
        self._end_stage(self._stage_title(stage))

    def show_stage_result(self, stage: str, content: str, reasoning: str) -> None:
        del content  # 正式回答在「决策」/「原始」页查看
        stage_id = "stage1" if "一" in stage else "stage2"
        if reasoning and stage_id == self._stage and self._reasoning_chars == 0:
            self._append_reasoning(reasoning)
        if stage_id not in self._finalized_stages:
            self.finalize_stage(stage_id)

    def _on_send_or_stop(self) -> None:
        if self._sending:
            if self._cancel_token is not None:
                self._cancel_token.set()
        else:
            self._on_send()

    def _on_send(self) -> None:
        if self._session is None:
            return
        text = self._input_edit.toPlainText().strip()
        if not text:
            return
        from pa_agent.util.threading import CancelToken

        self._cancel_token = CancelToken()
        self._input_edit.clear()

        self._begin_stage("chat", "追问")
        self._append_user_message(text)
        self._phase_label.setText("▶ 追问 — 生成中…")

        self._sending = True
        self._send_btn.setText("停止")
        self._send_btn.setObjectName("dangerButton")
        self._send_btn.style().unpolish(self._send_btn)
        self._send_btn.style().polish(self._send_btn)
        self._input_edit.setEnabled(False)

        self._worker = _ChatWorker(self._session, text, self._cancel_token, parent=self)
        self._worker.reasoning_token.connect(self._append_reasoning)
        self._worker.finished.connect(self._on_reply_done)
        self._worker.error.connect(self._on_reply_error)
        self._worker.finished.connect(lambda *_: self._on_worker_done())
        self._worker.error.connect(lambda *_: self._on_worker_done())
        self._worker.start()

    def _on_reply_done(self, content: str, reasoning: str) -> None:
        del content
        if reasoning and self._reasoning_chars == 0:
            self._append_reasoning(reasoning)
        self._end_stage("追问")
        if self._session is not None:
            ledger = getattr(self._session, "_ledger", None)
            if ledger is not None and hasattr(ledger, "breakdown"):
                bd = ledger.breakdown()
                if bd:
                    self.update_token_display(bd)

    def _on_reply_error(self, msg: str) -> None:
        self._append_reasoning(f"\n[错误] {msg}\n")
        self._end_stage("追问（失败）")

    def _on_worker_done(self) -> None:
        self._sending = False
        self._send_btn.setText("发送")
        self._send_btn.setObjectName("primaryButton")
        self._send_btn.style().unpolish(self._send_btn)
        self._send_btn.style().polish(self._send_btn)
        self._input_edit.setEnabled(True)
        self._worker = None
