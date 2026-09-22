"""SignalLoader / SimulationEngine 选股配置修复测试。"""

from __future__ import annotations

from backend.services.simulation.engine import _extract_strategy_config_kwargs
from backend.services.simulation.services.signal_loader import _sql_limit_clause


def test_sql_limit_clause_omits_when_none():
    assert _sql_limit_clause(None) == ""
    assert _sql_limit_clause(0) == ""
    assert _sql_limit_clause(-1) == ""
    assert _sql_limit_clause(1000) == " LIMIT :limit"


def test_extract_strategy_config_kwargs_n_drop():
    code = '''
STRATEGY_CONFIG = {
    "class": "RedisTopkStrategy",
    "kwargs": {
        "signal": "<PRED>",
        "topk": 50,
        "n_drop": 10,
    }
}
'''
    kwargs = _extract_strategy_config_kwargs(code)
    assert kwargs["topk"] == 50
    assert kwargs["n_drop"] == 10


def test_extract_strategy_config_kwargs_empty():
    assert _extract_strategy_config_kwargs("") == {}
    assert _extract_strategy_config_kwargs("x = 1") == {}
