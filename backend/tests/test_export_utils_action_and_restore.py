"""回归测试：回测导出「成交明细」口径修复。

覆盖两个曾导致导出异常的缺陷：
1. action 口径：risk_analyzer._parse_trades_df 产出 buy_to_open / sell_to_close /
   buy_to_cover / sell_to_open，旧代码只判断 == "buy"，把这些全写成「卖出」。
2. 复权回算死分支：旧判定 `explicit_quantity is None` 在归一化后恒为 False，
   factor 形同白取；改为 quantity 非整手（复权小数股数）时才回算。
"""

from backend.services.engine.qlib_app.api.export_utils import (
    _build_quick_trade_rows,
    _resolve_trade_action,
)


def test_resolve_trade_action_covers_all_writer_dialects():
    assert _resolve_trade_action("buy") == "买入"
    assert _resolve_trade_action("sell") == "卖出"
    assert _resolve_trade_action("buy_to_open") == "买入"
    assert _resolve_trade_action("buy_to_cover") == "买入"
    assert _resolve_trade_action("sell_to_close") == "卖出"
    assert _resolve_trade_action("sell_to_open") == "卖出"
    assert _resolve_trade_action("") == "卖出"


def test_build_quick_trade_rows_maps_four_way_actions():
    trades = [
        {"date": "2026-01-05", "symbol": "SH600036", "action": "buy_to_open",
         "price": 10.0, "quantity": 100, "totalAmount": 1000.0, "commission": 1.0},
        {"date": "2026-01-06", "symbol": "SH600036", "action": "sell_to_close",
         "price": 11.0, "quantity": 100, "totalAmount": 1100.0, "commission": 1.1},
        {"date": "2026-01-07", "symbol": "SZ000001", "action": "sell_to_open",
         "price": 5.0, "quantity": 200, "totalAmount": 1000.0, "commission": 1.0},
        {"date": "2026-01-08", "symbol": "SZ000001", "action": "buy_to_cover",
         "price": 5.5, "quantity": 200, "totalAmount": 1100.0, "commission": 1.1},
    ]
    rows = _build_quick_trade_rows(trades=trades, equity_curve=[], initial_capital=100000.0)

    assert [r["action"] for r in rows] == ["买入", "卖出", "卖出", "买入"]


def test_build_quick_trade_rows_restores_non_lot_quantity_with_factor():
    """quantity 是复权小数股数（非整手）时，用 factor 还原为非复权口径。"""
    trades = [
        {
            "date": "2026-01-05",
            "symbol": "SH600036",
            "action": "buy",
            "price": 20.0,          # 复权价
            "quantity": 63.7,       # 复权小数股数 → 非整手
            "adj_price": 20.0,
            "adj_quantity": 63.7,
            "factor": 2.0,
            "totalAmount": 1274.0,
            "commission": 0.0,
        }
    ]
    rows = _build_quick_trade_rows(trades=trades, equity_curve=[], initial_capital=10000.0)

    assert len(rows) == 1
    # 20.0 / 2.0 = 10.0 价格；63.7 * 2.0 = 127.4 → 取整 127 股
    # （127 距整手 100 超过 2 股，不会被整手回吸）
    assert rows[0]["display_price"] == 10.0
    assert rows[0]["qty_int"] == 127
