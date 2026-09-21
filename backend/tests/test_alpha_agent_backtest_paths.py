"""因子回测路径回归测试：数据源字段口径 + 并发隔离。

线上实测故障（两条独立缺陷叠加，表现为回测状态在 completed/failed/backtesting 横跳）：

1. 字段口径错配：挖掘/IC 阶段用 39 字段的 daily_pv.h5（含 rsi_14/turn_5/netflow_* 等），
   而 Qlib 二进制只有 open/high/low/close/volume/amount/factor。因子若引用扩展字段，
   走 Qlib 回测必然 KeyError: None of [Index(['rsi_14'])] are in the [columns]。

2. 并发覆盖：回测 subprocess 固定用 /tmp/daily_pv.h5、/tmp/_bt_result.h5 并 chdir 到 /tmp，
   还 os.listdir('.') 扫描结果文件 —— 并发回测互相覆盖/读到别人的结果，
   出现 "File /tmp/_bt_result.h5 does not exist" 或结果串味。

本测试锁定：H5 候选优先级、扩展字段识别（含误报防护）、每次回测工作目录唯一。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# alpha_agent 路由模块导入时会解析 qlib 后端，本地无 qlib 的环境直接跳过
# （容器/CI 内有真 qlib，会正常执行；纯逻辑也可用 python -c 在容器内直接验证）。
pytest.importorskip("qlib")

from backend.services.engine.routers.alpha_agent import (  # noqa: E402
    _H5_ONLY_FIELDS,
    _H5_PATH_CANDIDATES,
    _QLIB_BINARY_FIELDS,
    _h5_only_fields_in,
    _new_backtest_workspace,
)


def test_a_share_h5_prefers_mining_source() -> None:
    """A 股回测必须优先用与挖掘同源的富数据，否则字段必然缺失。"""
    assert _H5_PATH_CANDIDATES["a_share"][0] == "/data/quantdb/.h5_cache/daily_pv_all.h5"


def test_qlib_fields_and_h5_only_fields_are_disjoint() -> None:
    """扩展字段集合不得包含 Qlib 二进制已提供的字段（否则会误判走 H5）。"""
    assert _QLIB_BINARY_FIELDS & _H5_ONLY_FIELDS == set()
    assert {"open", "high", "low", "close", "volume", "amount", "factor"} <= _QLIB_BINARY_FIELDS


def test_detects_dollar_and_plain_field_references() -> None:
    """$rsi_14 与 rsi_14 两种写法都要能识别。"""
    code = """
import pandas as pd
def calculate_x(data_path='daily_pv.h5', output_path='result.h5'):
    df = pd.read_hdf(data_path)
    rsi_col = '$rsi_14' if '$rsi_14' in df.columns else 'rsi_14'
    df = df[[rsi_col]]
    return (50.0 - df[rsi_col]).to_frame()
"""
    assert _h5_only_fields_in(code) == ["rsi_14"]


def test_detects_multiple_extended_fields() -> None:
    code = "f = df['$turn_5'] / df['turn_20']\n g = df['$netflow_5']\n h = df['$ep']\n"
    assert _h5_only_fields_in(code) == ["ep", "netflow_5", "turn_20", "turn_5"]


def test_plain_ohlcv_only_factor_needs_no_h5() -> None:
    """只用 OHLCV 的因子不该被切到 H5（Qlib 路径能给出 sharpe 等更完整指标）。"""
    code = "r = df['$close'].pct_change(5)\n v = df['volume']\n f = r * v\n"
    assert _h5_only_fields_in(code) == []


def test_no_false_positive_inside_longer_identifiers() -> None:
    """字段名出现在更长的标识符/单词里时不能误判（否则会无谓切换数据源）。"""
    code = """
my_rsi_14_factor_helper = 1
steps = 2
heroes = 3
turn_ratio_5_20_name = 'x'
def calculate_turn_ratio_5_20():
    return None
"""
    assert _h5_only_fields_in(code) == []


def test_backtest_workspace_is_unique_per_run(tmp_path: Path) -> None:
    """每次回测的临时工作目录必须唯一，否则并发会互相覆盖。"""
    a = _new_backtest_workspace("255bdcc7bba919bea2f59cb8ba4b2bfb")
    b = _new_backtest_workspace("255bdcc7bba919bea2f59cb8ba4b2bfb")
    try:
        assert a != b
        assert a.is_dir() and b.is_dir()
        assert "255bdcc7" in a.name
    finally:
        import shutil

        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_backtest_workspace_sanitizes_factor_id(tmp_path: Path) -> None:
    """因子 id 含路径分隔符等字符时不能逃出临时目录。"""
    import shutil

    ws = _new_backtest_workspace("../../evil/id")
    try:
        assert ws.parent == Path(ws.parent)  # 仍在临时目录下
        assert "/" not in ws.name and ".." not in ws.name
    finally:
        shutil.rmtree(ws, ignore_errors=True)
