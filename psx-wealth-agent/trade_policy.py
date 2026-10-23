"""Cost-aware, read-only PSX trade policy for research.

No live orders, automatic buy approvals, or guessed commission/tax rates.
Settings are scenario assumptions: investors must provide their broker's actual
charges and applicable NCCPL tax status before using numerical levels.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from math import floor, isfinite, sqrt

PHI = (1.0 + sqrt(5.0)) / 2.0


@dataclass(frozen=True)
class TradePolicy:
    capital_pkr: float = 25_000.0
    risk_of_capital_pct: float = 3.0
    target_price_gain_pct: float = 7.0
    minimum_net_reward_risk: float = PHI
    commission_each_side_pct: float = 0.15
    slippage_each_side_pct: float = 0.0
    capital_gains_tax_pct: float = 15.0

    def validate(self) -> None:
        if not isfinite(self.capital_pkr) or self.capital_pkr <= 0:
            raise ValueError("Capital must be positive")
        if not 0 < self.risk_of_capital_pct <= 10:
            raise ValueError("Risk-of-capital must be 0-10 percent")
        if not 0 < self.target_price_gain_pct <= 100:
            raise ValueError("Target price gain must be 0-100 percent")
        if not 1 <= self.minimum_net_reward_risk <= 10:
            raise ValueError("Minimum net reward/risk must be at least 1")
        for amount in (self.commission_each_side_pct,
                       self.slippage_each_side_pct, self.capital_gains_tax_pct):
            if not isfinite(amount) or not 0 <= amount < 100:
                raise ValueError("Fee and tax rates must be finite percentages")
        if self.commission_each_side_pct + self.slippage_each_side_pct >= 100:
            raise ValueError("Combined execution costs cannot exceed 100 percent")


def evaluate_trade(entry: float, structural_stop: float,
                   policy: TradePolicy = TradePolicy(),
                   previously_at_risk_pkr: float = 0.0) -> dict:
    """Assess a BUY scenario; do not move a structural stop to force a ratio.

    Tax estimate applies CGT to positive gross price appreciation; the exact
    taxable base, deductions, levy and holding-period treatment can differ.
    Fees and slippage are assessed on both entry and exit. All monetary values
    are in PKR and quantities in whole shares.
    """
    policy.validate()
    if not all(map(isfinite, (entry, structural_stop, previously_at_risk_pkr))):
        raise ValueError("Prices and existing portfolio risk must be finite")
    if not (entry > structural_stop > 0):
        raise ValueError("Structural stop must be below a positive entry")
    if previously_at_risk_pkr < 0:
        raise ValueError("Previously committed risk cannot be negative")

    execution_cost = (policy.commission_each_side_pct +
                      policy.slippage_each_side_pct) / 100.0
    tax_rate = policy.capital_gains_tax_pct / 100.0
    target = entry * (1.0 + policy.target_price_gain_pct / 100.0)
    cash_to_buy_one = entry * (1.0 + execution_cost)
    net_gain_one = (target * (1.0 - execution_cost) - cash_to_buy_one
                    - max(target - entry, 0.0) * tax_rate)
    net_loss_one = cash_to_buy_one - structural_stop * (1.0 - execution_cost)
    rr = net_gain_one / net_loss_one if net_loss_one > 0 else None
    capital_risk_limit = policy.capital_pkr * policy.risk_of_capital_pct / 100.0
    remaining_risk_limit = max(0.0, capital_risk_limit - previously_at_risk_pkr)
    quantity = max(0, min(
        floor(policy.capital_pkr / cash_to_buy_one),
        floor(remaining_risk_limit / net_loss_one),
    ))
    # A higher permissible stop is not a recommended stop. A credible chart
    # level lower than this boundary means decline the trade.
    minimum_stop_for_phi = (
        cash_to_buy_one - net_gain_one / policy.minimum_net_reward_risk
    ) / (1.0 - execution_cost) if net_gain_one > 0 else None

    reasons = []
    if net_gain_one <= 0:
        reasons.append("7% gross gain would not cover this tax/fee scenario")
    if rr is None or rr < policy.minimum_net_reward_risk:
        reasons.append("Structural stop fails minimum after-cost reward/risk")
    if quantity == 0:
        reasons.append("Insufficient capital or risk budget for one share")
    status = "RESEARCH_ONLY_POLICY_PASSED" if not reasons else "DO_NOT_ENTER_POLICY_FAILED"

    return {
        "status": status,
        "reasons": reasons,
        "entry_pkr": round(entry, 4),
        "structural_stop_pkr": round(structural_stop, 4),
        "price_target_pkr": round(target, 4),
        "gross_target_pct": policy.target_price_gain_pct,
        "golden_ratio_minimum": round(policy.minimum_net_reward_risk, 5),
        "net_reward_risk": round(rr, 5) if rr is not None else None,
        "net_profit_per_share_pkr": round(net_gain_one, 4),
        "net_loss_per_share_pkr": round(net_loss_one, 4),
        "maximum_stop_risk_budget_pkr": round(capital_risk_limit, 2),
        "remaining_risk_budget_pkr": round(remaining_risk_limit, 2),
        "maximum_whole_shares": quantity,
        "capital_required_pkr": round(quantity * cash_to_buy_one, 2),
        "estimated_stop_loss_pkr": round(quantity * net_loss_one, 2),
        "estimated_target_profit_after_costs_pkr": round(quantity * net_gain_one, 2),
        "lowest_stop_price_satisfying_net_ratio_pkr": (
            round(minimum_stop_for_phi, 4)
            if minimum_stop_for_phi is not None else None
        ),
        "assumptions": {
            "commission_each_side_pct": policy.commission_each_side_pct,
            "slippage_each_side_pct": policy.slippage_each_side_pct,
            "cgt_on_positive_gross_gain_pct": policy.capital_gains_tax_pct,
            "note": "Assumptions, not verified Stock Hub contract charges or tax advice",
        },
        "buy_approved": False,
        "orders_submitted": 0,
    }


def trailing_pe(price_pkr: float, trailing_eps_pkr: float) -> float | None:
    """Price/TTM EPS; non-positive earnings mean conventional P/E is N/M."""
    if not all(map(isfinite, (price_pkr, trailing_eps_pkr))) or price_pkr <= 0:
        raise ValueError("Price must be finite and positive; EPS finite")
    if trailing_eps_pkr <= 0:
        return None
    return round(price_pkr / trailing_eps_pkr, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline, hypothetical PSX trade review")
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument("--stop", type=float, required=True)
    parser.add_argument("--capital", type=float, default=25000.0)
    parser.add_argument("--risk-pct", type=float, default=3.0)
    parser.add_argument("--target-pct", type=float, default=7.0)
    parser.add_argument("--commission-pct", type=float, default=0.15)
    parser.add_argument("--slippage-pct", type=float, default=0.0)
    parser.add_argument("--cgt-pct", type=float, default=15.0)
    parser.add_argument("--already-at-risk-pkr", type=float, default=0.0)
    args = parser.parse_args()
    policy = TradePolicy(capital_pkr=args.capital, risk_of_capital_pct=args.risk_pct,
                         target_price_gain_pct=args.target_pct,
                         commission_each_side_pct=args.commission_pct,
                         slippage_each_side_pct=args.slippage_pct,
                         capital_gains_tax_pct=args.cgt_pct)
    import json
    print(json.dumps(evaluate_trade(args.entry, args.stop, policy,
                                    args.already_at_risk_pkr), indent=2))


if __name__ == "__main__":
    main()
