"""Offline tests for the hypothetical golden-ratio/3-percent trading policy."""
import math
import unittest

from trade_policy import PHI, TradePolicy, evaluate_trade, trailing_pe


class TradePolicyTests(unittest.TestCase):
    def test_phi_is_not_two_and_budget_is_seven_hundred_fifty(self):
        self.assertAlmostEqual(PHI, 1.61803398875, places=8)
        result = evaluate_trade(100.0, 97.0)
        self.assertEqual(result["maximum_stop_risk_budget_pkr"], 750.0)
        self.assertEqual(result["status"], "RESEARCH_ONLY_POLICY_PASSED")
        self.assertLessEqual(result["estimated_stop_loss_pkr"], 750.0)
        self.assertLessEqual(result["capital_required_pkr"], 25000.0)
        self.assertGreaterEqual(result["net_reward_risk"], PHI)

    def test_seven_percent_price_appreciation_is_not_net_return(self):
        result = evaluate_trade(100.0, 97.0)
        self.assertEqual(result["price_target_pkr"], 107.0)
        self.assertAlmostEqual(result["net_profit_per_share_pkr"], 5.6395, places=4)
        self.assertLess(result["net_profit_per_share_pkr"], 7.0)

    def test_grossly_optimistic_stop_fails_after_tax_and_fees(self):
        result = evaluate_trade(100.0, 96.0)
        self.assertEqual(result["status"], "DO_NOT_ENTER_POLICY_FAILED")
        self.assertLess(result["net_reward_risk"], PHI)
        self.assertFalse(result["buy_approved"])

    def test_shares_reduced_by_committed_portfolio_risk(self):
        first = evaluate_trade(100.0, 97.0)
        second = evaluate_trade(100.0, 97.0, previously_at_risk_pkr=650)
        self.assertLess(second["maximum_whole_shares"], first["maximum_whole_shares"])
        self.assertLessEqual(second["estimated_stop_loss_pkr"], 100.0)

    def test_gap_and_tax_cannot_guarantee_real_world_stop(self):
        base = evaluate_trade(100.0, 97.0)
        with_slippage = evaluate_trade(
            100.0, 97.0, TradePolicy(slippage_each_side_pct=0.5))
        self.assertLess(with_slippage["net_reward_risk"], base["net_reward_risk"])

    def test_invalid_stops_or_inputs_rejected(self):
        for entry, stop in [(100, 100), (100, 101), (100, -1),
                            (float("nan"), 95)]:
            with self.assertRaises(ValueError):
                evaluate_trade(entry, stop)
        with self.assertRaises(ValueError):
            evaluate_trade(100, 97, previously_at_risk_pkr=-1)

    def test_trailing_pe_handles_losses_without_absurd_negative_pe(self):
        self.assertEqual(trailing_pe(100.0, 5.0), 20.0)
        self.assertIsNone(trailing_pe(100.0, 0.0))
        self.assertIsNone(trailing_pe(100.0, -2.0))


if __name__ == "__main__":
    unittest.main()
