import unittest
from unittest.mock import AsyncMock, patch
from app.infrastructure.blockchain.tx_utils import calculate_onchain_executed_price, fetch_transaction_details


class TestOnChainSlippageCapture(unittest.TestCase):
    def test_calculate_onchain_executed_price_success(self):
        """
        Verifies correct executed_price_usd calculation from net SOL spent (minus fee)
        and net token received.
        """
        wallet_address = "WalletA1111111111111111111111111111111111"
        token_mint = "TokenX11111111111111111111111111111111111"
        sol_price_usd = 150.0

        tx_details = {
            "slot": 123456,
            "meta": {
                "err": None,
                "fee": 5000,  # 0.000005 SOL fee
                "preBalances": [10_000_000_000, 1_000_000],  # 10.0 SOL
                "postBalances": [9_495_005_000, 1_000_000],  # 9.495005 SOL
                # Net SOL spent = (10 - 9.495005) - 0.000005 = 0.504990 SOL
                "preTokenBalances": [
                    {
                        "accountIndex": 2,
                        "mint": token_mint,
                        "owner": wallet_address,
                        "uiTokenAmount": {"uiAmount": 0.0}
                    }
                ],
                "postTokenBalances": [
                    {
                        "accountIndex": 2,
                        "mint": token_mint,
                        "owner": wallet_address,
                        "uiTokenAmount": {"uiAmount": 1000.0}
                    }
                ]
            },
            "transaction": {
                "message": {
                    "accountKeys": [
                        {"pubkey": wallet_address, "signer": True},
                        {"pubkey": "SystemProgram111111111111111111111111111111", "signer": False},
                        {"pubkey": "TokenAccountA1111111111111111111111111111", "signer": False}
                    ]
                }
            }
        }

        executed_price_usd = calculate_onchain_executed_price(
            tx_details=tx_details,
            wallet_address=wallet_address,
            token_mint=token_mint,
            sol_price_usd=sol_price_usd
        )

        # Net SOL spent = 0.504990 SOL * $150 = $75.7485
        # Net Token received = 1000.0
        # Executed price = $75.7485 / 1000.0 = $0.0757485
        self.assertIsNotNone(executed_price_usd)
        self.assertAlmostEqual(executed_price_usd, 0.0757485, places=6)

        # Test slippage math
        quoted_price_usd = 0.0750
        slippage_actual = (quoted_price_usd - executed_price_usd) / quoted_price_usd
        self.assertAlmostEqual(slippage_actual, (0.0750 - 0.0757485) / 0.0750)

    def test_calculate_onchain_executed_price_transaction_err_returns_none(self):
        """Verifies that an on-chain transaction error returns None (null)."""
        tx_details = {
            "meta": {
                "err": {"InstructionError": [0, "Custom"]},
                "preBalances": [10000000],
                "postBalances": [9000000]
            }
        }

        res = calculate_onchain_executed_price(
            tx_details=tx_details,
            wallet_address="WalletA",
            token_mint="TokenA",
            sol_price_usd=150.0
        )

        self.assertIsNone(res)

    def test_calculate_onchain_executed_price_token2022_account_index_matching(self):
        """Verifies Token-2022 accountIndex resolution when owner field is indirect."""
        wallet_address = "WalletToken2022_1111111111111111111111111"
        token_mint = "Token2022Mint_11111111111111111111111111"

        tx_details = {
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [5_000_000_000],
                "postBalances": [3_999_995_000],
                "preTokenBalances": [
                    {
                        "accountIndex": 1,
                        "mint": token_mint,
                        "uiTokenAmount": {"uiAmount": 100.0}
                    }
                ],
                "postTokenBalances": [
                    {
                        "accountIndex": 1,
                        "mint": token_mint,
                        "uiTokenAmount": {"uiAmount": 600.0}
                    }
                ]
            },
            "transaction": {
                "message": {
                    "accountKeys": [
                        wallet_address,
                        wallet_address  # Account index 1 is also wallet_address
                    ]
                }
            }
        }

        # Net SOL spent = 1.0 SOL * $150 = $150.0
        # Net Token received = 500.0
        # Executed price = $150 / 500 = $0.30
        executed_price = calculate_onchain_executed_price(
            tx_details=tx_details,
            wallet_address=wallet_address,
            token_mint=token_mint,
            sol_price_usd=150.0
        )

        self.assertIsNotNone(executed_price)
        self.assertAlmostEqual(executed_price, 0.30)

    def test_calculate_onchain_executed_price_negative_or_zero_token_received_returns_none(self):
        """Verifies that non-positive token balance delta returns None (null)."""
        tx_details = {
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [5_000_000_000],
                "postBalances": [4_000_000_000],
                "preTokenBalances": [
                    {"accountIndex": 0, "mint": "TokenA", "owner": "WalletA", "uiTokenAmount": {"uiAmount": 500.0}}
                ],
                "postTokenBalances": [
                    {"accountIndex": 0, "mint": "TokenA", "owner": "WalletA", "uiTokenAmount": {"uiAmount": 400.0}}  # Token decreased
                ]
            },
            "transaction": {
                "message": {"accountKeys": ["WalletA"]}
            }
        }

        res = calculate_onchain_executed_price(
            tx_details=tx_details,
            wallet_address="WalletA",
            token_mint="TokenA",
            sol_price_usd=150.0
        )

        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
