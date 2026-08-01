import unittest
import struct
from unittest.mock import patch, AsyncMock
from solders.pubkey import Pubkey

from app.infrastructure.blockchain.bonding_curve_price import (
    get_bonding_curve_pda,
    parse_bonding_curve_account_data,
    get_bonding_curve_price,
    estimate_bonding_curve_price_impact
)


class TestBondingCurvePrice(unittest.TestCase):
    def test_pda_derivation(self):
        token_mint = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
        pda = get_bonding_curve_pda(token_mint)
        self.assertIsNotNone(pda)
        self.assertIsInstance(pda, Pubkey)

    def test_struct_unpacking(self):
        # Build 49-byte dummy struct payload (v_token first, then v_sol)
        discriminator = b"\x00" * 8
        v_token = 1_000_000_000_000 # 1,000,000 tokens (with 6 decimals)
        v_sol = 30_000_000_000      # 30.0 SOL in lamports
        r_token = 800_000_000_000
        r_sol = 10_000_000_000
        supply = 1_000_000_000_000
        complete = 0 # False

        payload = discriminator + struct.pack("<QQQQQB", v_token, v_sol, r_token, r_sol, supply, complete)
        self.assertEqual(len(payload), 49)

        parsed = parse_bonding_curve_account_data(payload)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["virtualSolReserves"], v_sol)
        self.assertEqual(parsed["virtualTokenReserves"], v_token)
        self.assertFalse(parsed["complete"])

    def test_pump_fun_official_readme_fixture(self):
        """
        Regression test using official account fixture from pump.fun documentation (PUMP_PROGRAM_README.md):
        Reference: https://github.com/pump-fun/pump-public-docs/blob/main/PUMP_PROGRAM_README.md
        v_token_reserves = 1072999999992855 (1,072,999,999.992855 tokens)
        v_sol_reserves   = 30000000013 (30.000000013 SOL)
        Expected Price in SOL = 30.000000013 / 1072999999.992855 = 0.0000000279589932... SOL
        For SOL = $150 USD -> Expected Price in USD = $0.00000419384899... USD (~$0.00000419 USD)
        """
        discriminator = b"\x00" * 8
        v_token = 1_072_999_999_992_855
        v_sol = 30_000_000_013
        r_token = 793_000_000_000_000
        r_sol = 10_000_000_013
        supply = 1_000_000_000_000_000
        complete = 0

        payload = discriminator + struct.pack("<QQQQQB", v_token, v_sol, r_token, r_sol, supply, complete)
        parsed = parse_bonding_curve_account_data(payload)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["virtualTokenReserves"], v_token)
        self.assertEqual(parsed["virtualSolReserves"], v_sol)

        sol_reserves = parsed["virtualSolReserves"] / 1e9
        token_reserves = parsed["virtualTokenReserves"] / 1e6
        price_sol = sol_reserves / token_reserves
        price_usd = price_sol * 150.0

        # Assert correct order of magnitude ($0.00000419 USD, NOT $5,365 USD!)
        self.assertAlmostEqual(price_usd, 0.00000419384899, places=8)

    @patch("app.infrastructure.blockchain.bonding_curve_price.fetch_bonding_curve_account_info", new_callable=AsyncMock)
    def test_get_bonding_curve_price(self, mock_fetch):
        mock_fetch.return_value = {
            "virtualSolReserves": 30_000_000_000,       # 30 SOL
            "virtualTokenReserves": 1_000_000_000_000, # 1,000,000 tokens
            "realSolReserves": 0,
            "realTokenReserves": 0,
            "tokenTotalSupply": 1000000000000,
            "complete": False
        }

        import asyncio
        price = asyncio.run(get_bonding_curve_price("4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", sol_price_usd=150.0))

        self.assertIsNotNone(price)
        self.assertAlmostEqual(price, 0.0045, places=6)

    @patch("app.infrastructure.blockchain.bonding_curve_price.fetch_bonding_curve_account_info", new_callable=AsyncMock)
    def test_estimate_bonding_curve_price_impact_monotonic(self, mock_fetch):
        """Verifies price impact scales monotonically higher for larger trade sizes."""
        mock_fetch.return_value = {
            "virtualSolReserves": 30_000_000_000,       # 30 SOL
            "virtualTokenReserves": 1_000_000_000_000, # 1M tokens
            "realSolReserves": 0,
            "realTokenReserves": 0,
            "tokenTotalSupply": 1000000000000,
            "complete": False
        }

        import asyncio
        impact_small = asyncio.run(estimate_bonding_curve_price_impact("4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", sol_amount_in=0.1))
        impact_medium = asyncio.run(estimate_bonding_curve_price_impact("4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", sol_amount_in=1.0))
        impact_large = asyncio.run(estimate_bonding_curve_price_impact("4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", sol_amount_in=5.0))

        self.assertIsNotNone(impact_small)
        self.assertIsNotNone(impact_medium)
        self.assertIsNotNone(impact_large)

        # Monotonic property check: 0.1 SOL impact < 1.0 SOL impact < 5.0 SOL impact
        self.assertLess(impact_small, impact_medium)
        self.assertLess(impact_medium, impact_large)


if __name__ == "__main__":
    unittest.main()
