"""市场定时同步调度器测试。

覆盖 _normalize 的建议时间预填（suggested_time：01:00-06:00 随机、10 分钟整数倍）、
未配置时保持关闭、以及显式保存配置的覆盖行为，Redis 用桩对象替代。
"""

from __future__ import annotations

import pytest

from backend.services.engine.tasks.market_sync_scheduler import (
    DEFAULT_SCHEDULE,
    MARKETS,
    SUGGESTED_TIME_STEP_MINUTES,
    SUGGESTED_TIME_WINDOW,
    get_schedule,
    save_schedule,
    suggested_time,
)


def _in_window(t: str) -> bool:
    return SUGGESTED_TIME_WINDOW[0] <= t <= SUGGESTED_TIME_WINDOW[1]


def _on_step_grid(t: str) -> bool:
    hour, minute = t.split(":")
    return int(minute) % SUGGESTED_TIME_STEP_MINUTES == 0


class _StubRedis:
    """最小 Redis 桩：仅 get/set/exists，单测不依赖真实 Redis。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    def exists(self, key: str) -> bool:
        return key in self._data


@pytest.fixture()
def stub_redis(monkeypatch: pytest.MonkeyPatch) -> _StubRedis:
    stub = _StubRedis()
    monkeypatch.setattr(
        "backend.services.engine.tasks.market_sync_scheduler._redis", lambda: stub
    )
    return stub


def test_no_market_is_enabled_without_user_config(stub_redis):
    # Act：Redis 里没有任何配置时逐一读取所有市场
    got = {m: get_schedule(m) for m in MARKETS}

    # Assert：所有市场一律保持关闭，避免所有部署在同一固定时刻全量同步
    assert all(cfg["enabled"] is False for cfg in got.values()), got


def test_market_suggested_time_is_prefilled_without_enabling(stub_redis):
    # Act：Redis 里没有任何 HK 配置时读取
    cfg = get_schedule("HK")

    # Assert：建议时间只作预填，enabled 仍为 False；其余字段沿用全局默认
    assert cfg["enabled"] is False
    assert _in_window(cfg["time"]), cfg["time"]
    assert _on_step_grid(cfg["time"]), cfg["time"]
    assert cfg["days"] == DEFAULT_SCHEDULE["days"]
    assert cfg["datasets"] == []


def test_suggested_time_is_random_in_window_on_10min_grid():
    # Act：多次取值
    times = {suggested_time() for _ in range(200)}

    # Assert：全部落在 01:00-06:00 且为 10 分钟整数倍
    assert all(_in_window(t) for t in times), times
    assert all(_on_step_grid(t) for t in times), times
    # 随机性：200 次抽样不应恒定不变（窗口内共 31 个候选）
    assert len(times) > 1, times


def test_suggested_time_covers_full_window():
    # Assert：窗口边界可被取到（含 01:00 与 06:00），不会退化成固定值
    seen = {suggested_time() for _ in range(3000)}
    assert SUGGESTED_TIME_WINDOW[0] in seen, seen
    assert SUGGESTED_TIME_WINDOW[1] in seen, seen


def test_ashare_stays_disabled_without_config(stub_redis):
    # Act：A 股未配置
    cfg = get_schedule("A")

    # Assert：保持关闭，时间落在建议窗口内
    assert cfg["enabled"] is False
    assert _in_window(cfg["time"]), cfg["time"]


def test_user_can_enable_market_explicitly(stub_redis):
    # Arrange：用户在前端显式开启港股定时
    save_schedule("HK", {"enabled": True, "time": "22:30"})

    # Act
    cfg = get_schedule("HK")

    # Assert：以用户保存的配置为准
    assert cfg["enabled"] is True
    assert cfg["time"] == "22:30"


def test_explicit_saved_config_disables_market(stub_redis):
    # Arrange：用户在前端显式关闭港股定时
    save_schedule("HK", {"enabled": False})

    # Act
    cfg = get_schedule("HK")

    # Assert：显式关闭生效；未覆盖字段沿用建议时间预填
    assert cfg["enabled"] is False
    assert _in_window(cfg["time"]), cfg["time"]


def test_save_and_get_roundtrip_keeps_fields_not_set_by_caller(stub_redis):
    # Arrange：只传 enabled/time 的部分配置
    saved = save_schedule("HK", {"enabled": True, "time": "22:30"})

    # Act
    loaded = get_schedule("HK")

    # Assert：保存与读回一致，调用方未传字段沿用默认值
    assert saved == loaded
    assert loaded["time"] == "22:30"
    assert loaded["days"] == DEFAULT_SCHEDULE["days"]


def test_invalid_time_in_stored_config_falls_back_to_global_default(stub_redis):
    # Arrange：绕过 API 层校验，直接把坏时间写进 Redis 配置
    save_schedule("HK", {"time": "25:00"})

    # Act
    cfg = get_schedule("HK")

    # Assert：非法 HH:MM 回退到全局默认时间而不是抛错
    assert cfg["time"] == "03:00"


def test_normalize_of_missing_config_for_unknown_market_uses_global_defaults():
    from backend.services.engine.tasks.market_sync_scheduler import _normalize

    # Act：未知 market 传入时仅应用全局默认，不抛错
    got = _normalize(None, "XX")

    # Assert：除建议时间外与全局默认完全一致（时间为窗口内的随机预填值）
    assert got == {**DEFAULT_SCHEDULE, "time": got["time"]}
    assert _in_window(got["time"]), got["time"]
