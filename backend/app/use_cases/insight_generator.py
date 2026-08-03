"""
F-02 / FR-101 & FR-102: AI Market Insight Pipeline (LLM Hypothesis Generator)
Summarizes closed trade data, requests hypothesis generation from Anthropic LLM API,
and passes hypotheses through StatisticalValidator.
Completely isolated from trade execution pipeline.
"""
import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from app.core.config import settings
from app.domain.models import ClosedTrade, MarketInsight
from app.use_cases.insight_statistical_validator import StatisticalValidator

logger = logging.getLogger(__name__)


def summarize_trade_data_for_prompt(trades: List[ClosedTrade]) -> Dict[str, Any]:
    """
    Summarizes closed trades into structured statistical metrics to send to LLM
    (prevents sending raw trade logs, saving token budget).
    """
    total = len(trades)
    if total == 0:
        return {"total_trades": 0}

    wins = [t for t in trades if t.label == "BUY_BENAR" or t.r_multiple > 0]
    losses = [t for t in trades if t.label == "SALAH" or t.r_multiple <= 0]

    by_exit_reason: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        reason = t.exit_reason or "unknown"
        if reason not in by_exit_reason:
            by_exit_reason[reason] = {"count": 0, "wins": 0, "total_r": 0.0}
        by_exit_reason[reason]["count"] += 1
        if t.label == "BUY_BENAR" or t.r_multiple > 0:
            by_exit_reason[reason]["wins"] += 1
        by_exit_reason[reason]["total_r"] += t.r_multiple

    for r, data in by_exit_reason.items():
        cnt = data["count"]
        data["win_rate"] = round(data["wins"] / cnt, 3) if cnt > 0 else 0.0
        data["avg_r"] = round(data["total_r"] / cnt, 3) if cnt > 0 else 0.0

    by_conf_tier: Dict[str, Dict[str, Any]] = {"high_0.8+": {"count": 0, "wins": 0}, "med_0.65-0.8": {"count": 0, "wins": 0}, "low_<0.65": {"count": 0, "wins": 0}}
    for t in trades:
        score = t.confidence_score
        tier = "high_0.8+" if score >= 0.8 else ("med_0.65-0.8" if score >= 0.65 else "low_<0.65")
        by_conf_tier[tier]["count"] += 1
        if t.label == "BUY_BENAR" or t.r_multiple > 0:
            by_conf_tier[tier]["wins"] += 1

    for tier, data in by_conf_tier.items():
        cnt = data["count"]
        data["win_rate"] = round(data["wins"] / cnt, 3) if cnt > 0 else 0.0

    return {
        "total_trades": total,
        "overall_win_rate": round(len(wins) / total, 3),
        "overall_avg_r": round(sum(t.r_multiple for t in trades) / total, 3),
        "by_exit_reason": by_exit_reason,
        "by_confidence_tier": by_conf_tier
    }


class InsightGeneratorJob:
    """
    FR-101 to FR-106 AI Market Insight Generator & Statistical Validator Pipeline
    """
    def __init__(
        self,
        trade_history_repo: Any,
        market_insight_repo: Any,
        statistical_validator: Optional[StatisticalValidator] = None
    ):
        self.trade_history_repo = trade_history_repo
        self.market_insight_repo = market_insight_repo
        self.validator = statistical_validator or StatisticalValidator()

    def _parse_json_hypotheses(self, content_text: str) -> Optional[List[Dict[str, str]]]:
        """Helper to parse JSON array of hypotheses from LLM response text."""
        if not content_text:
            return None
        text = content_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except Exception as e:
            logger.warning(f"[INSIGHT GENERATOR] Failed to parse JSON hypotheses from LLM output: {e}")
        return None

    async def _call_llm(self, summary_json: str) -> List[Dict[str, str]]:
        """
        Multi-provider LLM caller with automatic fallback.
        Order: Groq -> OpenRouter -> DeepSeek -> Together -> Gemini -> Anthropic.
        """
        model_name = getattr(settings, "LLM_MODEL", "llama-3.3-70b-versatile") or getattr(settings, "INSIGHT_MODEL_NAME", "llama-3.3-70b-versatile")

        system_prompt = (
            "You are an expert quantitative crypto trading analyst. "
            "Analyze the provided historical trading summary metrics and generate 1 to 3 explicit hypotheses "
            "about conditions that could improve win rate or expectancy. "
            "Your output MUST be a valid JSON array of objects with keys:\n"
            " - 'hypothesis': clear textual description of the hypothesis\n"
            " - 'affected_condition': valid Python boolean expression using variables: "
            "confidence_score, holding_time_minutes, position_size_usd, risk_pct, pnl_pct_actual, r_multiple, exit_reason, label, direction\n"
            "Example:\n"
            "[\n"
            "  {\n"
            "    \"hypothesis\": \"Trades held longer than 30 minutes with confidence >= 0.75 yield higher win rate\",\n"
            "    \"affected_condition\": \"holding_time_minutes > 30 and confidence_score >= 0.75\"\n"
            "  }\n"
            "]\n"
            "OUTPUT ONLY THE RAW JSON ARRAY. DO NOT INCLUDE ANY MARKDOWN CODE BLOCKS OR EXTRA TEXT."
        )
        user_prompt = f"Historical Trade Summary Data:\n{summary_json}"

        providers = [
            {
                "name": "Groq",
                "key": os.environ.get("GROQ_API_KEY") or getattr(settings, "GROQ_API_KEY", ""),
                "url": "https://api.groq.com/openai/v1/chat/completions",
                "model": model_name,
                "type": "openai"
            },
            {
                "name": "OpenRouter",
                "key": os.environ.get("OPENROUTER_API_KEY") or getattr(settings, "OPENROUTER_API_KEY", ""),
                "url": "https://openrouter.ai/api/v1/chat/completions",
                "model": "meta-llama/llama-3.3-70b-instruct" if model_name == "llama-3.3-70b-versatile" else model_name,
                "type": "openai"
            },
            {
                "name": "DeepSeek",
                "key": os.environ.get("DEEPSEEK_API_KEY") or getattr(settings, "DEEPSEEK_API_KEY", ""),
                "url": "https://api.deepseek.com/v1/chat/completions",
                "model": "deepseek-chat",
                "type": "openai"
            },
            {
                "name": "Together AI",
                "key": os.environ.get("TOGETHER_API_KEY") or getattr(settings, "TOGETHER_API_KEY", ""),
                "url": "https://api.together.xyz/v1/chat/completions",
                "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "type": "openai"
            },
            {
                "name": "Gemini",
                "key": os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", ""),
                "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                "model": "gemini-1.5-flash",
                "type": "openai"
            },
            {
                "name": "Anthropic",
                "key": os.environ.get("ANTHROPIC_API_KEY") or getattr(settings, "ANTHROPIC_API_KEY", ""),
                "url": "https://api.anthropic.com/v1/messages",
                "model": "claude-3-5-sonnet-20241022",
                "type": "anthropic"
            }
        ]

        try:
            import httpx
        except ImportError:
            import httpx2 as httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            for prov in providers:
                api_key = prov["key"]
                if not api_key:
                    continue

                provider_name = prov["name"]
                logger.info(f"[INSIGHT GENERATOR] Attempting LLM hypothesis generation via '{provider_name}'...")

                if prov["type"] == "openai":
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": prov["model"],
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.2
                    }
                    try:
                        res = await client.post(prov["url"], headers=headers, json=payload)
                        if res.status_code == 200:
                            data = res.json()
                            content = data["choices"][0]["message"]["content"]
                            parsed = self._parse_json_hypotheses(content)
                            if parsed:
                                logger.info(f"[INSIGHT GENERATOR] [SUCCESS] LLM hypotheses generated via '{provider_name}'!")
                                return parsed
                        else:
                            logger.warning(f"[INSIGHT GENERATOR] Provider '{provider_name}' HTTP {res.status_code}: {res.text[:150]}")
                    except Exception as e:
                        logger.warning(f"[INSIGHT GENERATOR] Provider '{provider_name}' failed: {e}")

                elif prov["type"] == "anthropic":
                    headers = {
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    }
                    payload = {
                        "model": prov["model"],
                        "max_tokens": 1000,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}]
                    }
                    try:
                        res = await client.post(prov["url"], headers=headers, json=payload)
                        if res.status_code == 200:
                            data = res.json()
                            content = data.get("content", [{}])[0].get("text", "")
                            parsed = self._parse_json_hypotheses(content)
                            if parsed:
                                logger.info(f"[INSIGHT GENERATOR] [SUCCESS] LLM hypotheses generated via 'Anthropic'!")
                                return parsed
                        else:
                            logger.warning(f"[INSIGHT GENERATOR] Provider 'Anthropic' HTTP {res.status_code}: {res.text[:150]}")
                    except Exception as e:
                        logger.warning(f"[INSIGHT GENERATOR] Provider 'Anthropic' failed: {e}")

        logger.warning("[INSIGHT GENERATOR] No active LLM provider keys succeeded. Skipping LLM hypothesis generation.")
        return []

    async def run_insight_pipeline(self) -> List[MarketInsight]:
        """
        Executes complete pipeline:
        1. Fetch closed trades.
        2. Generate LLM hypotheses via multi-provider fallback client.
        3. Validate hypotheses statistically.
        4. Save insights to DB repository.
        """
        logger.info("[INSIGHT GENERATOR] Starting AI Market Insight pipeline execution...")
        results: List[MarketInsight] = []
        try:
            trades: List[ClosedTrade] = await self.trade_history_repo.get_closed_trades(limit=1000)
            if not trades:
                logger.info("[INSIGHT GENERATOR] No closed trade history found in database. Insight job finished.")
                return []

            summary_data = summarize_trade_data_for_prompt(trades)
            summary_json = json.dumps(summary_data, indent=2)

            hypotheses = await self._call_llm(summary_json)
            if not hypotheses:
                logger.info("[INSIGHT GENERATOR] No new hypotheses generated by LLM.")
                return []

            for item in hypotheses:
                hyp_text = item.get("hypothesis", "")
                aff_cond = item.get("affected_condition", "")
                if not hyp_text or not aff_cond:
                    continue

                insight = self.validator.validate_hypothesis(
                    hypothesis_text=hyp_text,
                    affected_condition=aff_cond,
                    trades=trades
                )

                await self.market_insight_repo.add_insight(insight)
                results.append(insight)
                logger.info(
                    f"[INSIGHT GENERATOR] Stored MarketInsight {insight.insight_id}: "
                    f"status={insight.statistical_status}, dWR={insight.win_rate_diff:+.1%}"
                )

            return results
        except Exception as e:
            logger.error(f"[INSIGHT GENERATOR] Unexpected error in insight pipeline: {e}", exc_info=True)
            return []
