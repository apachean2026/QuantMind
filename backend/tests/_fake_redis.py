"""测试用内存版 Redis 客户端。

只实现测试涉及的命令子集（string / set / list / 风控 Lua）。
原先定义在 ``backend/tests/test_qmt_exec_mirror.py``，因该用例已删除，
抽到本模块供其它用例复用。
"""

from __future__ import annotations

from typing import Any


class FakeRedisClient:
    """内存版 Redis（只实现被测代码用到的命令）。"""

    def __init__(
        self,
        *,
        strings: dict[str, str] | None = None,
        sets: dict[str, set[str]] | None = None,
    ):
        self.strings: dict[str, str] = dict(strings or {})
        self.sets: dict[str, set[str]] = {k: set(v) for k, v in (sets or {}).items()}
        self.lists: dict[str, list[str]] = {}

    # -- string --
    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def exists(self, key: str) -> int:
        return int(key in self.strings or key in self.sets)

    def set(self, key: str, value: Any) -> bool:
        self.strings[key] = str(value)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += 1 if self.strings.pop(key, None) is not None else 0
            removed += 1 if self.sets.pop(key, None) is not None else 0
        return removed

    def incr(self, key: str) -> int:
        value = int(float(self.strings.get(key) or 0)) + 1
        self.strings[key] = str(value)
        return value

    def decr(self, key: str) -> int:
        value = int(float(self.strings.get(key) or 0)) - 1
        self.strings[key] = str(value)
        return value

    def incrbyfloat(self, key: str, amount: float) -> float:
        value = float(self.strings.get(key) or 0) + float(amount)
        self.strings[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        return True

    # -- set --
    def sadd(self, key: str, *values: str) -> int:
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(str(v) for v in values)
        return len(target) - before

    def srem(self, key: str, *values: str) -> int:
        target = self.sets.get(key) or set()
        removed = 0
        for value in values:
            removed += 1 if value in target else 0
            target.discard(str(value))
        return removed

    def scard(self, key: str) -> int:
        return len(self.sets.get(key) or set())

    def sismember(self, key: str, value: str) -> bool:
        return str(value) in (self.sets.get(key) or set())

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key) or set())

    # -- list --
    def rpush(self, key: str, *values: str) -> int:
        target = self.lists.setdefault(key, [])
        target.extend(str(v) for v in values)
        return len(target)

    def lpop(self, key: str) -> str | None:
        target = self.lists.get(key) or []
        return target.pop(0) if target else None

    def llen(self, key: str) -> int:
        return len(self.lists.get(key) or [])

    def eval(self, script: str, numkeys: int, *args: Any) -> list[Any]:
        """对风控 ``_RESERVE_LUA`` 语义做最小模拟（返回值统一为 bytes）。

        真实 Lua 已在真实 Redis 验证；这里只保证服务端能正确解码。
        """
        keys = args[:numkeys]
        argv = args[numkeys:]
        value, symbol = float(argv[0]), str(argv[1])
        max_order, max_daily, max_orders, max_symbols = (
            float(argv[2]),
            float(argv[3]),
            float(argv[4]),
            float(argv[5]),
        )
        daily_value = float(self.strings.get(keys[0]) or 0)
        daily_orders = float(self.strings.get(keys[1]) or 0)
        symbols = self.sets.get(keys[2]) or set()
        new_flag = 0 if symbol in symbols else 1

        def reject(reason: str) -> list[bytes]:
            return [
                b"0",
                reason.encode(),
                str(daily_value).encode(),
                str(daily_orders).encode(),
                str(len(symbols)).encode(),
                str(new_flag).encode(),
            ]

        if value > max_order:
            return reject("max_order_value")
        if daily_value + value > max_daily:
            return reject("max_daily_value")
        if daily_orders + 1 > max_orders:
            return reject("max_daily_orders")
        if new_flag == 1 and len(symbols) + 1 > max_symbols:
            return reject("max_daily_symbols")
        self.incrbyfloat(keys[0], value)
        self.incr(keys[1])
        self.sadd(keys[2], symbol)
        return [
            b"1",
            b"ok",
            str(daily_value + value).encode(),
            str(daily_orders + 1).encode(),
            str(len(symbols) + new_flag).encode(),
            str(new_flag).encode(),
        ]
