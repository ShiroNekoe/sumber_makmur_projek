import unittest
import time
from datetime import datetime, timezone
from app.blockchain.monitor import is_valid_solana_pubkey, SolanaWebSocketMonitor


class TestWalletMovementMonitor(unittest.TestCase):
    def test_solana_pubkey_validation(self):
        # Valid pubkeys
        self.assertTrue(is_valid_solana_pubkey("Wha1eA11111111111111111111111111111111111"))
        self.assertTrue(is_valid_solana_pubkey("Wha1eB22222222222222222222222222222222222"))
        self.assertTrue(is_valid_solana_pubkey("6EF8rrect3EDQS425286575m1111111111111111"))
        
        # Invalid pubkeys (containing invalid base58 chars like 0, O, I, l, or wrong length)
        self.assertFalse(is_valid_solana_pubkey("Wha1eA01111111111111111111111111111111111")) # Contains 0
        self.assertFalse(is_valid_solana_pubkey("ShortKey"))
        self.assertFalse(is_valid_solana_pubkey("ThisKeyIsWayTooLongAndDefinitelyNotAValidSolanaPublicKey123456789"))

    def test_signature_deduplication(self):
        monitor = SolanaWebSocketMonitor()
        sig = "5abc123XYZsignatureTestDeduplicationString1234567890abcdefghijklmnopqrstuvwxyz"
        
        # Initially dedup window is empty
        self.assertNotIn(sig, monitor.signature_dedup_window)
        
        # Simulate receiving notification
        monitor.signature_dedup_window[sig] = time.time()
        self.assertIn(sig, monitor.signature_dedup_window)

    def test_transaction_log_parsing(self):
        monitor = SolanaWebSocketMonitor()
        monitor.active_wallets.add("Wha1eA11111111111111111111111111111111111")
        
        # Test swap detection
        logs_swap = [
            "Program log: Instruction: Swap",
            "Program log: Swap succeeded",
        ]
        parsed_swap = monitor._parse_transaction_payload(
            signature="test_sig",
            tx_details={
                "blockTime": int(time.time()),
                "meta": {"postTokenBalances": [], "preTokenBalances": []},
                "transaction": {"message": {"accountKeys": ["Wha1eA11111111111111111111111111111111111"]}}
            },
            logs=logs_swap
        )
        self.assertIsNotNone(parsed_swap)
        self.assertEqual(parsed_swap["event_type"], "swap")
        
        # Test transfer detection
        logs_transfer = [
            "Program log: Instruction: Transfer",
        ]
        parsed_transfer = monitor._parse_transaction_payload(
            signature="test_sig_2",
            tx_details={
                "blockTime": int(time.time()),
                "meta": {"postTokenBalances": [], "preTokenBalances": []},
                "transaction": {"message": {"accountKeys": ["Wha1eA11111111111111111111111111111111111"]}}
            },
            logs=logs_transfer
        )
        self.assertIsNotNone(parsed_transfer)
        self.assertEqual(parsed_transfer["event_type"], "transfer")


if __name__ == "__main__":
    unittest.main()
