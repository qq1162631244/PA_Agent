"""Prompt assembler for Stage 1 (diagnosis) and Stage 2 (decision)."""
from __future__ import annotations

import datetime
import json
import logging
import math
from pathlib import Path
from typing import Any

from pa_agent.data.base import KlineFrame

logger = logging.getLogger(__name__)

# ── Hardcoded output format reminders ─────────────────────────────────────────

_STAGE1_OUTPUT_REMINDER = """
请严格按照以下 JSON 格式输出诊断结果，不要输出任何其他内容：

```json
{
  "cycle_position": "spike|micro_channel|tight_channel|normal_channel|broad_channel|trending_tr|trading_range|extreme_tr|unknown",
  "alternative_cycle_position": null,
  "direction": "bullish|bearish|neutral",
  "diagnosis_confidence": 75,
  "spike_stage": null,
  "market_phase": "stable|transitioning",
  "transition_risk": null,
  "detected_patterns": [],
  "key_signals": [],
  "htf_context": "",
  "entry_setup": "",
  "strategy_files_needed": [],
  "risk_warning": ""
}
```

diagnosis_confidence 必须为 0–100 的整数（满分100），表示对 cycle_position 等诊断结论的综合置信评分。
禁止使用 high、medium、low 等字符串；分数越高表示对当前市场状态判断越有把握。
""".strip()

_STAGE2_OUTPUT_CONTRACT = """
请严格按照以下 JSON 格式输出决策结果，不要输出任何其他内容。
重要规则：当 order_type 为"不下单"时，entry_price、take_profit_price、stop_loss_price、order_direction 必须全部为 null。

```json
{
  "decision": {
    "order_direction": "做多|做空|null",
    "order_type": "限价单|突破单|市价单|不下单",
    "entry_price": null,
    "take_profit_price": null,
    "stop_loss_price": null,
    "reasoning": "",
    "confidence": 75,
    "key_factors": [],
    "watch_points": [],
    "risk_assessment": "",
    "invalidation_condition": ""
  },
  "diagnosis_summary": {
    "cycle_position": "",
    "direction": "",
    "key_signals": []
  }
}
```

confidence 字段说明（无论是否下单都必须填写整数 0-100）：
- 表示对「本次决策」的综合把握：下单时表示入场方案可信度；不下单时表示对「观望/等待」判断的把握（非入场信心）
- 90-100：极高把握，结构清晰、理由充分
- 70-89：较高把握，主要逻辑明确
- 50-69：中等把握，存在不确定性但仍可执行当前决策（含观望）
- 30-49：较低把握，建议继续等待更清晰信号
- 0-29：极低把握；若同时判断不应交易，可配合 order_type="不下单"
""".strip()

# txt files merged into each stage system prompt (order preserved)
STAGE1_PROMPT_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
    "市场诊断框架.txt",
    "文件16-K线信号识别.txt",
)

STAGE2_BASE_PROMPT_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
    "文件17-止损和止盈与仓位管理.txt",
)


def stage1_prompt_txt_files() -> list[str]:
    """Return ordered .txt filenames injected in Stage 1 system prompt."""
    return list(STAGE1_PROMPT_TXT_FILES)


def stage2_prompt_txt_files(strategy_files: list[str] | None = None) -> list[str]:
    """Return ordered .txt filenames injected in Stage 2 system prompt."""
    routed = [f for f in (strategy_files or []) if f]
    return [STAGE2_BASE_PROMPT_TXT_FILES[0], *routed, STAGE2_BASE_PROMPT_TXT_FILES[1]]


# ── PromptAssembler ────────────────────────────────────────────────────────────

class PromptAssembler:
    """Builds message lists for Stage 1 and Stage 2 API calls."""

    def __init__(
        self,
        prompt_dir: Path,
        experience_reader: Any = None,
    ) -> None:
        self._prompt_dir = prompt_dir
        self._experience_reader = experience_reader

    # ── File loading ──────────────────────────────────────────────────────────

    def _load(self, filename: str) -> str:
        """Load a prompt file by name. Returns empty string on error."""
        path = self._prompt_dir / filename
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to load prompt file %s: %s", filename, exc)
            return f"[ERROR: could not load {filename}]"

    # ── K-line table rendering ────────────────────────────────────────────────

    @staticmethod
    def _render_kline_table(frame: KlineFrame) -> str:
        """Render the K-line data as a text table (newest bar first)."""
        lines = [
            "序号 | 时间                | 开盘价    | 最高价    | 最低价    | 收盘价    | 成交量    | EMA20     | ATR14",
            "-----+--------------------+----------+----------+----------+----------+----------+-----------+----------",
        ]
        for i, bar in enumerate(frame.bars):
            ema = frame.indicators.ema20[i]
            atr = frame.indicators.atr14[i]
            ema_str = f"{ema:.4f}" if not math.isnan(ema) else "N/A"
            atr_str = f"{atr:.4f}" if not math.isnan(atr) else "N/A"
            # ts_open is in milliseconds (MT5 source); convert to seconds for fromtimestamp()
            dt = datetime.datetime.fromtimestamp(bar.ts_open / 1000).strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"{bar.seq:<4} | {dt:<19} | {bar.open:<9.4f} | {bar.high:<9.4f} | "
                f"{bar.low:<9.4f} | {bar.close:<9.4f} | {bar.volume:<9.0f} | "
                f"{ema_str:<10} | {atr_str}"
            )
        return "\n".join(lines)

    # ── Stage 1 ───────────────────────────────────────────────────────────────

    def build_stage1(self, frame: KlineFrame) -> list[dict]:
        """Build the message list for Stage 1 (market diagnosis)."""
        system_parts = [
            *(self._load(name) for name in STAGE1_PROMPT_TXT_FILES),
            _STAGE1_OUTPUT_REMINDER,
        ]
        system_content = "\n\n" + "\n\n---\n\n".join(p for p in system_parts if p)

        kline_table = self._render_kline_table(frame)
        user_content = (
            f"## 当前分析目标\n\n"
            f"品种：{frame.symbol}　周期：{frame.timeframe}　K线数量：{len(frame.bars)}\n\n"
            f"## K线数据（序号1=最新已收盘K线，序号越大越早；不含当前未收盘K线）\n\n"
            f"{kline_table}\n\n"
            f"请根据以上数据，按照系统提示中的格式输出 JSON 诊断结果。"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    # ── Stage 2 ───────────────────────────────────────────────────────────────

    def build_stage2(
        self,
        frame: KlineFrame,
        stage1_json: dict,
        strategy_files: list[str],
        experience_entries: list[Any],
    ) -> list[dict]:
        """Build the message list for Stage 2 (trading decision)."""
        # System prompt: 人设 → 策略文件 → 风控 → 经验 → 输出契约
        system_parts = [self._load(name) for name in stage2_prompt_txt_files(strategy_files)]

        if experience_entries:
            exp_text = self._render_experience(experience_entries)
            system_parts.append(exp_text)

        system_parts.append(_STAGE2_OUTPUT_CONTRACT)

        system_content = "\n\n" + "\n\n---\n\n".join(p for p in system_parts if p)

        # User prompt
        kline_table = self._render_kline_table(frame)
        user_content = (
            f"## 阶段一诊断结果\n\n```json\n{json.dumps(stage1_json, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## K线数据（与阶段一相同）\n\n{kline_table}\n\n"
            f"请根据以上诊断结果和K线数据，按照系统提示中的格式输出 JSON 决策结果。\n"
            f"注意：如果判断不下单，entry_price、take_profit_price、stop_loss_price、order_direction 必须全部为 null。"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def stage2_system_prompt_only(
        self,
        strategy_files: list[str],
        experience_entries: list[Any],
    ) -> str:
        """Return only the Stage 2 system prompt string (for FreeChatSession reuse)."""
        system_parts = [self._load(name) for name in stage2_prompt_txt_files(strategy_files)]
        if experience_entries:
            system_parts.append(self._render_experience(experience_entries))
        system_parts.append(_STAGE2_OUTPUT_CONTRACT)
        return "\n\n" + "\n\n---\n\n".join(p for p in system_parts if p)

    @staticmethod
    def _render_experience(entries: list[Any]) -> str:
        """Render experience library entries as a text block."""
        lines = ["## 经验库（最近案例，供参考）"]
        for i, entry in enumerate(entries, 1):
            if isinstance(entry, dict):
                lines.append(
                    f"\n### 案例 {i}\n```json\n{json.dumps(entry, ensure_ascii=False, indent=2)}\n```"
                )
            else:
                lines.append(f"\n### 案例 {i}\n{entry}")
        return "\n".join(lines)
