"""
Tests for cluster_logic.py
Covers:
  1. Parity: training-path (ReconstructedPosition objects) == serving-path (dict with datetime).
  2. Single-wallet zero detection (both paths).
  3. BUG #1 REGRESSION: serving-path with ISO-string timestamp (real format from _signal_log / _cluster_event_log).
  4. BUG #2 REGRESSION: two wallets buying same token → both recorded in _cluster_event_log (no dedup) → cluster_score=1.0.
  5. End-to-end: append_cluster_event() → get_all_cluster_events() → compute_cluster_score() (real production data flow).
  6. Graceful skip on unparseable event timestamp (no crash).
"""
import unittest
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.domain.cluster_logic import (
    compute_cluster_score,
    append_cluster_event,
    get_all_cluster_events,
    _normalize_ts,
    GenericTradeEvent,
    _cluster_event_log,
)
from app.ml_pipeline.bootstrap import ReconstructedPosition, TokenMarketSnapshot


def _clear_cluster_log():
    """Helper: clear the module-level _cluster_event_log between tests."""
    import app.domain.cluster_logic as cl
    cl._cluster_event_log.clear()


class TestClusterLogicSharedFunction(unittest.TestCase):
    def setUp(self):
        _clear_cluster_log()

    def test_shared_cluster_score_parity_between_train_and_serve(self):
        """
        Verifies that training-path (bootstrap ReconstructedPosition events) and
        serving-path (inference / trigger events with datetime objects) produce
        100% IDENTICAL outputs when evaluated against equivalent trade inputs.
        """
        now = datetime.now(timezone.utc)
        target_wallet = "WalletWhaleA11111111111111111111111111111"
        other_wallet = "WalletWhaleB22222222222222222222222222222"
        target_token = "MemeTokenABC11111111111111111111111111111"

        snap = TokenMarketSnapshot(price_usd=1.0, liquidity_usd=10000.0, volume_24h=2000.0)

        # 1. Training-path data structure: ReconstructedPosition (has datetime .entry_ts)
        train_event_1 = ReconstructedPosition(
            wallet_address=target_wallet,
            token_mint=target_token,
            entry_signature="sigA",
            exit_signature="sigA_exit",
            entry_ts=now,
            exit_ts=now + timedelta(minutes=15),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )
        train_event_2 = ReconstructedPosition(
            wallet_address=other_wallet,
            token_mint=target_token,
            entry_signature="sigB",
            exit_signature="sigB_exit",
            entry_ts=now + timedelta(minutes=3),  # 3 mins later (within window)
            exit_ts=now + timedelta(minutes=18),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )

        train_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[train_event_1, train_event_2],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        # 2. Serving-path data structure: dict with datetime timestamp objects
        serve_event_1 = {
            "wallet_address": target_wallet,
            "token_address": target_token,
            "timestamp": now          # datetime object
        }
        serve_event_2 = {
            "wallet_address": other_wallet,
            "token_address": target_token,
            "timestamp": now + timedelta(minutes=3)   # datetime object
        }

        serve_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[serve_event_1, serve_event_2],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        # PARITY CHECK: Both MUST yield 1.0 (multi-wallet cluster detected)
        self.assertEqual(train_score, 1.0)
        self.assertEqual(serve_score, 1.0)
        self.assertEqual(train_score, serve_score)

    def test_single_wallet_returns_zero_on_both_paths(self):
        now = datetime.now(timezone.utc)
        target_wallet = "WalletWhaleA11111111111111111111111111111"
        target_token = "SingleTokenXYZ11111111111111111111111111"

        snap = TokenMarketSnapshot(price_usd=1.0, liquidity_usd=10000.0, volume_24h=2000.0)

        train_single = ReconstructedPosition(
            wallet_address=target_wallet,
            token_mint=target_token,
            entry_signature="sigA",
            exit_signature="sigA_exit",
            entry_ts=now,
            exit_ts=now + timedelta(minutes=15),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )

        train_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[train_single],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        serve_single = {
            "wallet_address": target_wallet,
            "token_address": target_token,
            "timestamp": now
        }

        serve_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[serve_single],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        self.assertEqual(train_score, 0.0)
        self.assertEqual(serve_score, 0.0)
        self.assertEqual(train_score, serve_score)

    # ------------------------------------------------------------------
    # BUG #1 REGRESSION TEST
    # ------------------------------------------------------------------
    def test_cluster_score_handles_string_timestamp_from_signal_log(self):
        """
        Regression for Bug #1: compute_cluster_score CRASHED with TypeError when
        event['timestamp'] was an ISO-format string (as stored by append_signal_event /
        append_cluster_event via .isoformat()).

        This test uses the EXACT format produced by append_cluster_event():
            "timestamp": datetime.now(timezone.utc).isoformat()

        Must return a float (0.0 or 1.0), NOT raise TypeError.
        """
        now = datetime.now(timezone.utc)
        target_wallet = "WalletA_RegressionBug1"
        other_wallet = "WalletB_RegressionBug1"
        target_token = "TokenX_RegressionBug1"

        # Exact format produced by append_cluster_event() — isoformat string, not datetime
        events = [
            {
                "wallet_address": other_wallet,
                "token_mint": target_token,
                "timestamp": now.isoformat(),   # <-- string, the real format from _cluster_event_log
            }
        ]

        result = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,       # target is a datetime object
            events=events,
            window_minutes=5.0
        )

        # Must not crash. Other wallet bought same token within 0 seconds → 1.0
        self.assertIsInstance(result, float)
        self.assertEqual(result, 1.0)

    def test_cluster_score_handles_string_target_timestamp(self):
        """
        target_timestamp itself may also arrive as ISO string in some call paths.
        Must not crash.
        """
        now = datetime.now(timezone.utc)
        events = [{
            "wallet_address": "OtherWallet",
            "token_mint": "TokenY",
            "timestamp": now.isoformat(),
        }]

        result = compute_cluster_score(
            target_wallet="TargetWallet",
            target_token="TokenY",
            target_timestamp=now.isoformat(),  # target as string
            events=events,
            window_minutes=5.0
        )

        self.assertIsInstance(result, float)
        self.assertEqual(result, 1.0)

    def test_cluster_score_skips_unparseable_event_no_crash(self):
        """
        If one event has a completely unparseable timestamp, it must be skipped
        (with a logger.warning) and the rest of the events still processed.
        The function MUST NOT raise an exception.
        """
        now = datetime.now(timezone.utc)
        other_wallet = "WalletOther_SkipTest"

        events = [
            {
                "wallet_address": other_wallet,
                "token_mint": "TokenZ",
                "timestamp": "NOT_A_REAL_TIMESTAMP",   # unparseable
            },
            {
                "wallet_address": "WalletGood",
                "token_mint": "TokenZ",
                "timestamp": now.isoformat(),   # valid — should be counted
            },
        ]

        result = compute_cluster_score(
            target_wallet="WalletTarget",
            target_token="TokenZ",
            target_timestamp=now,
            events=events,
            window_minutes=5.0
        )

        # Bad event skipped, good event counted → 1.0
        self.assertIsInstance(result, float)
        self.assertEqual(result, 1.0)

    # ------------------------------------------------------------------
    # BUG #2 REGRESSION TEST — Non-deduped _cluster_event_log
    # ------------------------------------------------------------------
    def test_two_wallets_buying_same_token_both_recorded_in_cluster_log(self):
        """
        Regression for Bug #2: _signal_log deduplicates per-token per 30 min,
        which means Wallet B's event would be silently dropped if it bought the
        same token within 30 minutes of Wallet A.

        _cluster_event_log does NOT dedup. This test verifies:
          1. Both wallet events are present in get_all_cluster_events().
          2. compute_cluster_score correctly returns 1.0 for Wallet B evaluating
             Wallet A's token (i.e., cluster detected).
        """
        now = datetime.now(timezone.utc)
        wallet_a = "WalletA_ClusterDedup"
        wallet_b = "WalletB_ClusterDedup"
        token = "TokenCluster_NoDedup"

        # Wallet A triggers first
        append_cluster_event(wallet_address=wallet_a, token_mint=token, timestamp=now)

        # Wallet B triggers 2 minutes later (would be deduped by _signal_log, but NOT by _cluster_event_log)
        append_cluster_event(wallet_address=wallet_b, token_mint=token, timestamp=now + timedelta(minutes=2))

        events = get_all_cluster_events()

        # BOTH events must be present (no dedup)
        wallet_addresses_in_log = [e["wallet_address"] for e in events if e.get("token_mint") == token]
        self.assertIn(wallet_a, wallet_addresses_in_log, "Wallet A not found in cluster log")
        self.assertIn(wallet_b, wallet_addresses_in_log, "Wallet B not found in cluster log — was it deduped?")

        # When Wallet B's trigger is evaluated: Wallet A event should be detected → cluster_score=1.0
        score_for_wallet_b = compute_cluster_score(
            target_wallet=wallet_b,
            target_token=token,
            target_timestamp=now + timedelta(minutes=2),
            events=events,
            window_minutes=5.0
        )
        self.assertEqual(score_for_wallet_b, 1.0,
                         "cluster_score should be 1.0 for Wallet B when Wallet A bought same token 2 min earlier")

    def test_end_to_end_cluster_flow_append_then_compute(self):
        """
        End-to-end production flow:
          append_cluster_event() → get_all_cluster_events() → compute_cluster_score()

        This is the actual call sequence used by inference.py in serving-path.
        Verifies the full data pipeline is connected, not just the pure function in isolation.
        """
        now = datetime.now(timezone.utc)
        wallet_a = "WalletA_E2E"
        wallet_b = "WalletB_E2E"
        token = "TokenE2E_EndToEnd"

        # Simulate Wallet A triggering first (inference.py would call this)
        append_cluster_event(wallet_address=wallet_a, token_mint=token, timestamp=now)

        # Simulate Wallet B triggering 1 min later
        ts_b = now + timedelta(minutes=1)
        append_cluster_event(wallet_address=wallet_b, token_mint=token, timestamp=ts_b)

        # Wallet B evaluates cluster (reads from non-deduped log)
        events = get_all_cluster_events()
        score = compute_cluster_score(
            target_wallet=wallet_b,
            target_token=token,
            target_timestamp=ts_b,
            events=events,
            window_minutes=5.0
        )

        # Full E2E: must detect Wallet A's event → 1.0
        self.assertEqual(score, 1.0)

        # Verify timestamps in the log are ISO strings (as stored by append_cluster_event)
        token_events = [e for e in events if e.get("token_mint") == token]
        for ev in token_events:
            self.assertIsInstance(ev["timestamp"], str,
                                  "append_cluster_event must store timestamp as isoformat string")


class TestNormalizeTs(unittest.TestCase):
    """Unit tests for the _normalize_ts helper directly."""

    def test_datetime_with_tz(self):
        now = datetime.now(timezone.utc)
        result = _normalize_ts(now)
        self.assertEqual(result, now)

    def test_datetime_without_tz_gets_utc(self):
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = _normalize_ts(naive)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tzinfo)

    def test_iso_string_with_z(self):
        s = "2024-01-01T12:00:00Z"
        result = _normalize_ts(s)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, datetime)
        self.assertEqual(result.year, 2024)

    def test_iso_string_with_plus_offset(self):
        s = "2024-01-01T12:00:00+00:00"
        result = _normalize_ts(s)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, datetime)

    def test_iso_string_from_isoformat(self):
        now = datetime.now(timezone.utc)
        s = now.isoformat()
        result = _normalize_ts(s)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.timestamp(), now.timestamp(), places=3)

    def test_none_returns_none(self):
        self.assertIsNone(_normalize_ts(None))

    def test_unparseable_string_returns_none(self):
        self.assertIsNone(_normalize_ts("NOT_A_DATE"))


if __name__ == "__main__":
    unittest.main()
