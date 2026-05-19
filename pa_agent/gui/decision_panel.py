"""DecisionPanel — trading decision + market diagnosis summary."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_NO_ORDER = "不下单"

# Brooks cycle_position → 中文（市场周期 / 频谱位置）
_CYCLE_POSITION_ZH: dict[str, str] = {
    "spike": "尖峰 (Spike)",
    "micro_channel": "微型通道",
    "tight_channel": "窄通道",
    "normal_channel": "正常通道",
    "broad_channel": "宽通道",
    "trending_tr": "趋势型交易区间",
    "trading_range": "交易区间",
    "extreme_tr": "极端交易区间",
    "unknown": "未知",
}

# 以震荡为主的周期类型
_RANGE_CYCLES = frozenset({"trading_range", "extreme_tr", "trending_tr"})

_MARKET_PHASE_ZH: dict[str, str] = {
    "stable": "稳定",
    "transitioning": "过渡",
}


def _format_cycle_position(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _CYCLE_POSITION_ZH.get(key, raw or "—")


def _format_market_phase(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _MARKET_PHASE_ZH.get(key, raw or "—")


def _infer_trend_label(direction: str, cycle_position: str) -> str:
    """Map AI direction + cycle to 上涨 / 下跌 / 震荡."""
    cp = (cycle_position or "").strip().lower()
    d = (direction or "").strip().lower()

    if cp in _RANGE_CYCLES:
        return "震荡"

    if d == "bullish":
        return "上涨"
    if d == "bearish":
        return "下跌"
    if d == "neutral":
        return "震荡"

    # 通道类但未给方向时，根据周期名粗略判断
    if cp in ("spike", "micro_channel", "tight_channel"):
        return "趋势运行中"
    return "—"


def _trend_color(label: str) -> str:
    if label == "上涨":
        return "#3fb950"
    if label == "下跌":
        return "#f85149"
    if label in ("震荡", "趋势运行中"):
        return "#e6b800"
    return "#8b949e"


def _score_color(score: int) -> str:
    if score >= 70:
        return "#3fb950"
    if score >= 50:
        return "#e6b800"
    return "#f85149"


def _parse_score_100(value: object) -> int | None:
    """Parse 0–100 diagnosis confidence score."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    try:
        return max(0, min(100, int(float(str(value).strip()))))
    except (ValueError, TypeError):
        return None


class DecisionPanel(QWidget):
    """Renders market diagnosis + Stage-2 trading decision."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("AI 交易决策")
        title.setObjectName("toolbarTitle")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── 市场诊断（阶段一 / diagnosis_summary）────────────────────────────
        diag_title = QLabel("市场诊断")
        diag_title.setStyleSheet("font-weight: bold; color: #58a6ff;")
        layout.addWidget(diag_title)

        self._trend_label = QLabel("趋势判断：—")
        self._trend_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._trend_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; padding: 10px;"
            "background-color: #21262d; border-radius: 8px; color: #8b949e;"
        )
        layout.addWidget(self._trend_label)

        self._cycle_label = QLabel("市场周期：—")
        self._cycle_label.setWordWrap(True)
        self._cycle_label.setObjectName("mutedLabel")
        layout.addWidget(self._cycle_label)

        self._phase_label = QLabel("市场阶段：—")
        self._phase_label.setObjectName("mutedLabel")
        layout.addWidget(self._phase_label)

        diag_conf_title = QLabel("诊断置信度")
        diag_conf_title.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(diag_conf_title)

        self._diag_conf_bar = QProgressBar()
        self._diag_conf_bar.setRange(0, 100)
        self._diag_conf_bar.setTextVisible(True)
        self._diag_conf_bar.setFormat("%v / 100")
        self._diag_conf_bar.setMaximumHeight(22)
        layout.addWidget(self._diag_conf_bar)

        self._diag_conf_label = QLabel("—")
        self._diag_conf_label.setObjectName("mutedLabel")
        layout.addWidget(self._diag_conf_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        trade_title = QLabel("交易决策")
        trade_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(trade_title)

        self._conclusion_label = QLabel("—")
        self._conclusion_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._conclusion_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; padding: 12px;"
        )
        layout.addWidget(self._conclusion_label)

        self._details_widget = QWidget()
        details_layout = QVBoxLayout(self._details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(6)

        self._direction_label = QLabel()
        self._order_type_label = QLabel()
        self._entry_label = QLabel()
        self._tp_label = QLabel()
        self._sl_label = QLabel()

        for lbl in (
            self._direction_label,
            self._order_type_label,
            self._entry_label,
            self._tp_label,
            self._sl_label,
        ):
            lbl.setWordWrap(True)
            lbl.setObjectName("mutedLabel")
            details_layout.addWidget(lbl)

        layout.addWidget(self._details_widget)

        self._decision_conf_title = QLabel("决策信心")
        self._decision_conf_title.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(self._decision_conf_title)

        self._confidence_bar = QProgressBar()
        self._confidence_bar.setRange(0, 100)
        self._confidence_bar.setTextVisible(True)
        self._confidence_bar.setFormat("%v / 100")
        self._confidence_bar.setMaximumHeight(22)
        layout.addWidget(self._confidence_bar)

        self._confidence_label = QLabel()
        layout.addWidget(self._confidence_label)

        reasoning_title = QLabel("分析理由")
        reasoning_title.setStyleSheet("font-weight: bold; color: #a371f7;")
        layout.addWidget(reasoning_title)

        self._reasoning_edit = QTextEdit()
        self._reasoning_edit.setReadOnly(True)
        self._reasoning_edit.setObjectName("answerPane")
        self._reasoning_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._reasoning_edit, stretch=1)

        self.clear()

    def _apply_market_diagnosis(
        self,
        diagnosis_summary: dict | None,
        stage1_diagnosis: dict | None = None,
    ) -> None:
        """Fill trend / cycle from stage2 summary, fallback to stage1."""
        src: dict = {}
        if diagnosis_summary:
            src.update(diagnosis_summary)
        if stage1_diagnosis:
            for k, v in stage1_diagnosis.items():
                src.setdefault(k, v)

        direction = str(src.get("direction", "") or "")
        cycle_position = str(src.get("cycle_position", "") or "")
        alt_cycle = src.get("alternative_cycle_position")
        market_phase = str(src.get("market_phase", "") or "")
        diag_conf_raw = src.get("diagnosis_confidence")

        trend = _infer_trend_label(direction, cycle_position)
        color = _trend_color(trend)
        self._trend_label.setText(f"趋势判断：{trend}")
        self._trend_label.setStyleSheet(
            f"font-size: 20px; font-weight: bold; padding: 10px;"
            f"color: {color}; background-color: #21262d; border-radius: 8px;"
        )

        cycle_zh = _format_cycle_position(cycle_position)
        cycle_line = f"市场周期：{cycle_zh}"
        if alt_cycle:
            cycle_line += f"（备选：{_format_cycle_position(str(alt_cycle))}）"
        self._cycle_label.setText(cycle_line)

        if market_phase:
            phase_zh = _format_market_phase(market_phase)
            extra = ""
            risk = src.get("transition_risk")
            if market_phase == "transitioning" and risk:
                extra = f" · 过渡风险 {risk}"
            self._phase_label.setText(f"市场阶段：{phase_zh}{extra}")
            self._phase_label.setVisible(True)
        else:
            self._phase_label.setVisible(False)

        diag_score = _parse_score_100(diag_conf_raw)
        if diag_score is not None:
            c_color = _score_color(diag_score)
            self._diag_conf_bar.setValue(diag_score)
            self._diag_conf_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {c_color}; }}"
            )
            self._diag_conf_label.setText(f"评分 {diag_score} / 100")
            self._diag_conf_label.setStyleSheet(f"color: {c_color}; font-weight: bold;")
            self._diag_conf_bar.setVisible(True)
            self._diag_conf_label.setVisible(True)
        else:
            self._diag_conf_bar.setValue(0)
            self._diag_conf_bar.setVisible(False)
            self._diag_conf_label.setVisible(False)

    def _apply_decision_confidence(
        self, confidence: object, *, no_order: bool = False
    ) -> None:
        score = _parse_score_100(confidence)
        if score is None:
            self._confidence_bar.setValue(0)
            self._confidence_bar.setVisible(False)
            self._confidence_label.setVisible(False)
            self._decision_conf_title.setVisible(False)
            return

        c_color = _score_color(score)
        self._confidence_bar.setValue(score)
        self._confidence_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {c_color}; }}"
        )
        hint = "观望决策" if no_order else "入场决策"
        self._confidence_label.setText(f"评分 {score} / 100 · {hint}")
        self._confidence_label.setStyleSheet(f"color: {c_color}; font-weight: bold;")
        self._decision_conf_title.setVisible(True)
        self._confidence_bar.setVisible(True)
        self._confidence_label.setVisible(True)

    def set_decision(
        self,
        decision: dict,
        *,
        diagnosis_summary: dict | None = None,
        stage1_diagnosis: dict | None = None,
    ) -> None:
        self._apply_market_diagnosis(diagnosis_summary, stage1_diagnosis)

        order_type = decision.get("order_type", _NO_ORDER)
        reasoning = decision.get("reasoning", decision.get("brief_reasoning", ""))
        confidence = decision.get("confidence", None)

        if order_type == _NO_ORDER:
            self._conclusion_label.setText("不下单")
            self._conclusion_label.setStyleSheet(
                "font-size: 18px; font-weight: bold; padding: 12px;"
                "color: #8b949e; background-color: #21262d; border-radius: 8px;"
            )
            self._details_widget.setVisible(False)
            self._apply_decision_confidence(confidence, no_order=True)
        else:
            direction = decision.get("order_direction", "—")
            entry = decision.get("entry_price")
            tp = decision.get("take_profit_price")
            sl = decision.get("stop_loss_price")

            self._conclusion_label.setText(order_type)
            color = "#3fb950" if "多" in str(direction) else "#f85149"
            self._conclusion_label.setStyleSheet(
                f"font-size: 18px; font-weight: bold; padding: 12px;"
                f"color: {color}; background-color: #21262d; border-radius: 8px;"
            )

            self._direction_label.setText(f"方向  {direction}")
            self._order_type_label.setText(f"类型  {order_type}")
            self._entry_label.setText(
                f"入场  {entry:.5g}" if entry is not None else "入场  —"
            )
            self._tp_label.setText(f"止盈  {tp:.5g}" if tp is not None else "止盈  —")
            self._sl_label.setText(f"止损  {sl:.5g}" if sl is not None else "止损  —")

            self._details_widget.setVisible(True)
            self._apply_decision_confidence(confidence, no_order=False)

        self._reasoning_edit.setPlainText(str(reasoning) if reasoning else "")

    def clear(self) -> None:
        self._trend_label.setText("趋势判断：—")
        self._trend_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; padding: 10px;"
            "color: #6e7681; background-color: #21262d; border-radius: 8px;"
        )
        self._cycle_label.setText("市场周期：—")
        self._phase_label.setText("市场阶段：—")
        self._phase_label.setVisible(True)
        self._diag_conf_bar.setValue(0)
        self._diag_conf_bar.setVisible(False)
        self._diag_conf_label.setVisible(False)

        self._conclusion_label.setText("等待分析")
        self._conclusion_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; padding: 12px; color: #6e7681;"
        )
        self._details_widget.setVisible(False)
        self._decision_conf_title.setVisible(False)
        self._confidence_bar.setValue(0)
        self._confidence_bar.setVisible(False)
        self._confidence_label.setVisible(False)
        self._reasoning_edit.clear()
