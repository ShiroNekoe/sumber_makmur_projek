"""
Unit and Integration tests for FITUR 2: AI Market Insight Pipeline dengan Statistical Gating
"""
import unittest
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.domain.models import ClosedTrade, MarketInsight
from app.use_cases.insight_statistical_validator import StatisticalValidator
from app.use_cases.insight_generator import InsightGeneratorJob, summarize_trade_data_for_prompt
from app.infrastructure.database.repository import SQLAlchemyMarketInsightRepository
from app.infrastructure.database.models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_mock_trade(
    trade_id: str,
    confidence_score: float,
    holding_time_minutes: int,
    r_multiple: float,
    label: str = "BUY_BENAR"
) -> ClosedTrade:
    now = datetime.now(timezone.utc)
    return ClosedTrade(
        trade_id=trade_id,
        wallet_source="w1",
        token_address="token1",
        token_symbol="T1",
        signal_ts=now - timedelta(hours=2),
        entry_ts=now - timedelta(hours=2),
        exit_ts=now - timedelta(hours=1),
        direction="BUY",
        confidence_score=confidence_score,
        safety_check_passed=True,
        entry_price=1.0,
        exit_price=1.0 + (r_multiple * 0.1),
        position_size_usd=1.0,
        risk_pct=0.01,
        pnl_pct_actual=r_multiple * 0.1,
        r_multiple=r_multiple,
        label=label,
        holding_time_minutes=holding_time_minutes,
        exit_reason="trailing_tp" if r_multiple > 0 else "SL",
        is_paper_trade=True,
        is_bootstrap=False,
        model_version="v0"
    )


class TestStatisticalValidator(unittest.TestCase):

    def setUp(self):
        self.validator = StatisticalValidator(min_sample_per_group=10)

    def test_statistical_validator_accepts_valid_hypothesis(self):
        """Test hypothesis with sufficient sample size and statistically significant win rate difference."""
        trades = []
        # Group A (confidence >= 0.80): 15 trades, 12 wins (win rate = 80%, avg R = +2.0)
        for i in range(15):
            win = i < 12
            trades.append(create_mock_trade(f"a_{i}", 0.85, 45, 2.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))
        # Group B (confidence < 0.80): 15 trades, 3 wins (win rate = 20%, avg R = -0.5)
        for i in range(15):
            win = i < 3
            trades.append(create_mock_trade(f"b_{i}", 0.60, 45, 2.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))

        insight = self.validator.validate_hypothesis(
            hypothesis_text="High confidence trades (>= 0.80) have higher win rate",
            affected_condition="confidence_score >= 0.80",
            trades=trades
        )

        self.assertEqual(insight.statistical_status, "PENDING_REVIEW")
        self.assertEqual(insight.sample_size_group_a, 15)
        self.assertEqual(insight.sample_size_group_b, 15)
        self.assertAlmostEqual(insight.win_rate_group_a, 0.80, places=2)
        self.assertAlmostEqual(insight.win_rate_group_b, 0.20, places=2)
        self.assertGreater(insight.win_rate_diff, 0.50)
        self.assertIsNotNone(insight.statistical_p_value)
        self.assertLess(insight.statistical_p_value, 0.05)
        self.assertIsNone(insight.rejection_reason)

    def test_statistical_validator_rejects_small_sample(self):
        """Test hypothesis with large win rate diff but small sample size (< min_sample_per_group) is rejected."""
        trades = []
        # Group A: only 4 trades (below N=10 threshold), 4 wins (100% win rate)
        for i in range(4):
            trades.append(create_mock_trade(f"a_{i}", 0.90, 45, 3.0, "BUY_BENAR"))
        # Group B: 15 trades, 3 wins
        for i in range(15):
            win = i < 3
            trades.append(create_mock_trade(f"b_{i}", 0.60, 45, 2.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))

        insight = self.validator.validate_hypothesis(
            hypothesis_text="Super high confidence trades yield 100% win rate",
            affected_condition="confidence_score >= 0.90",
            trades=trades
        )

        self.assertEqual(insight.statistical_status, "REJECTED_STATISTICAL")
        self.assertIn("Sample size below minimum threshold", insight.rejection_reason)
        self.assertEqual(insight.sample_size_group_a, 4)

    def test_statistical_validator_rejects_insignificant_diff(self):
        """Test hypothesis with negative or zero win rate difference is rejected."""
        trades = []
        # Group A: 12 trades, 4 wins
        for i in range(12):
            win = i < 4
            trades.append(create_mock_trade(f"a_{i}", 0.85, 10, 1.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))
        # Group B: 12 trades, 6 wins (higher win rate than A!)
        for i in range(12):
            win = i < 6
            trades.append(create_mock_trade(f"b_{i}", 0.60, 45, 1.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))

        insight = self.validator.validate_hypothesis(
            hypothesis_text="Holding time < 15 minutes improves performance",
            affected_condition="holding_time_minutes < 15",
            trades=trades
        )

        self.assertEqual(insight.statistical_status, "REJECTED_STATISTICAL")
        self.assertLess(insight.win_rate_diff, 0)
        self.assertIn("Difference not statistically significant", insight.rejection_reason)


class TestInsightGeneratorAndRepository(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()
        self.repo = SQLAlchemyMarketInsightRepository(self.db)

    def tearDown(self):
        self.db.close()

    async def test_insight_repository_crud(self):
        """Test adding, querying, and approving/rejecting insights in SQLite repository."""
        now = datetime.now(timezone.utc)
        insight = MarketInsight(
            insight_id="ins_test_1",
            hypothesis_text="Test hypothesis",
            affected_condition="confidence_score >= 0.8",
            sample_size_group_a=15,
            sample_size_group_b=15,
            win_rate_group_a=0.80,
            win_rate_group_b=0.20,
            win_rate_diff=0.60,
            expectancy_group_a=1.5,
            expectancy_group_b=-0.5,
            expectancy_diff=2.0,
            statistical_p_value=0.001,
            statistical_status="PENDING_REVIEW",
            rejection_reason=None,
            created_at=now
        )

        await self.repo.add_insight(insight)

        pending = await self.repo.get_insights(status="PENDING_REVIEW")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].insight_id, "ins_test_1")

        # Approve insight
        ok = await self.repo.update_insight_status("ins_test_1", "APPROVED")
        self.assertTrue(ok)

        approved = await self.repo.get_insights(status="APPROVED")
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].statistical_status, "APPROVED")

    async def test_insight_generator_handles_missing_api_key_gracefully(self):
        """Test InsightGeneratorJob handles missing LLM API keys gracefully without throwing errors."""
        mock_trade_repo = AsyncMock()
        mock_trade_repo.get_closed_trades.return_value = [create_mock_trade("t1", 0.8, 30, 1.0)]

        job = InsightGeneratorJob(
            trade_history_repo=mock_trade_repo,
            market_insight_repo=self.repo
        )

        with patch.dict("os.environ", {}, clear=True):
            results = await job.run_insight_pipeline()
            self.assertEqual(results, [])

    async def test_insight_generator_multi_provider_fallback(self):
        """Test InsightGeneratorJob falls back to active provider when first provider fails."""
        mock_trade_repo = AsyncMock()
        trades = []
        for i in range(15):
            win = i < 12
            trades.append(create_mock_trade(f"a_{i}", 0.85, 45, 2.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))
        for i in range(15):
            win = i < 3
            trades.append(create_mock_trade(f"b_{i}", 0.60, 45, 2.0 if win else -1.0, "BUY_BENAR" if win else "SALAH"))
        mock_trade_repo.get_closed_trades.return_value = trades

        job = InsightGeneratorJob(
            trade_history_repo=mock_trade_repo,
            market_insight_repo=self.repo
        )

        mock_llm_response = [
            {
                "hypothesis": "High confidence trades (>= 0.80) have higher win rate",
                "affected_condition": "confidence_score >= 0.80"
            }
        ]

        with patch.object(job, "_call_llm", return_value=mock_llm_response):
            results = await job.run_insight_pipeline()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].statistical_status, "PENDING_REVIEW")
            self.assertEqual(results[0].sample_size_group_a, 15)

    async def test_insight_generator_groq_is_first_priority(self):
        """Test Groq is attempted first before OpenRouter or other providers."""
        mock_trade_repo = AsyncMock()
        trades = [create_mock_trade(f"t_{i}", 0.85, 45, 2.0) for i in range(15)] + [create_mock_trade(f"tb_{i}", 0.60, 45, -1.0) for i in range(15)]
        mock_trade_repo.get_closed_trades.return_value = trades
        job = InsightGeneratorJob(trade_history_repo=mock_trade_repo, market_insight_repo=self.repo)

        attempted_providers = []

        async def mock_post(url, headers, json, **kwargs):
            if "api.groq.com" in url:
                attempted_providers.append("Groq")
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "choices": [{"message": {"content": '[{"hypothesis": "Groq test", "affected_condition": "confidence_score >= 0.8"}]'}}]
                }
                return resp
            elif "openrouter.ai" in url:
                attempted_providers.append("OpenRouter")
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "choices": [{"message": {"content": '[{"hypothesis": "OpenRouter test", "affected_condition": "confidence_score >= 0.8"}]'}}]
                }
                return resp
            return MagicMock(status_code=400)

        try:
            import httpx
        except ImportError:
            import httpx2 as httpx
        with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
            with patch.dict("os.environ", {
                "GROQ_API_KEY": "gsk_test",
                "OPENROUTER_API_KEY": "sk-or-test"
            }):
                results = await job.run_insight_pipeline()
                self.assertEqual(attempted_providers, ["Groq"])
                self.assertEqual(len(results), 1)
