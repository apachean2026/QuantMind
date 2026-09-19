"""l2 flow 金额单位归一（万元 ↔ 元）单测。"""

from __future__ import annotations

import pandas as pd

from backend.shared.quantdb_flow_units import (
    detect_flow_money_scale_to_yuan,
    normalize_l2_flow_money_to_yuan,
)


def test_detect_wan_via_ratio_identity():
    # amount=万元，flow=万元 → flow/amount = ratio
    df = pd.DataFrame(
        {
            "symbol": [f"S{i:04d}.SZ" for i in range(30)],
            "amount": [10000.0] * 30,
            "flow_net_amount": [800.0] * 30,
            "flow_net_ratio": [0.08] * 30,
            "flow_buy_amount": [5000.0] * 30,
        }
    )
    assert detect_flow_money_scale_to_yuan(df) == 1e4
    out = normalize_l2_flow_money_to_yuan(df)
    assert out["flow_net_amount"].iloc[0] == 800.0 * 1e4
    assert out["flow_buy_amount"].iloc[0] == 5000.0 * 1e4
    # ratio 不动
    assert out["flow_net_ratio"].iloc[0] == 0.08


def test_detect_yuan_via_ratio_identity():
    # amount=万元，flow=元 → flow/(amount*1e4) = ratio
    df = pd.DataFrame(
        {
            "symbol": [f"S{i:04d}.SZ" for i in range(30)],
            "amount": [10000.0] * 30,
            "flow_net_amount": [8_000_000.0] * 30,
            "flow_net_ratio": [0.08] * 30,
            "flow_super_net": [1_000_000.0] * 30,
        }
    )
    assert detect_flow_money_scale_to_yuan(df) == 1.0
    out = normalize_l2_flow_money_to_yuan(df)
    assert out["flow_net_amount"].iloc[0] == 8_000_000.0
    assert out["flow_super_net"].iloc[0] == 1_000_000.0


def test_groupby_dt_mixed_units():
    wan = pd.DataFrame(
        {
            "dt": ["20260918"] * 30,
            "amount": [20000.0] * 30,
            "flow_net_amount": [2000.0] * 30,
            "flow_net_ratio": [0.1] * 30,
        }
    )
    yuan = pd.DataFrame(
        {
            "dt": ["20260917"] * 30,
            "amount": [20000.0] * 30,
            "flow_net_amount": [20_000_000.0] * 30,
            "flow_net_ratio": [0.1] * 30,
        }
    )
    df = pd.concat([wan, yuan], ignore_index=True)
    out = normalize_l2_flow_money_to_yuan(df)
    assert out.loc[out["dt"] == "20260918", "flow_net_amount"].iloc[0] == 2000.0 * 1e4
    assert out.loc[out["dt"] == "20260917", "flow_net_amount"].iloc[0] == 20_000_000.0
