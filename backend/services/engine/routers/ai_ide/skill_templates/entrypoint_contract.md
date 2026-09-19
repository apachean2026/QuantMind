用途：AI-IDE 执行入口契约（最高优先级，生成或修复代码前必须遵守）。

## 二选一，禁止混用

运行器按顺序识别入口。**同一文件只能选一种模式**，混用会导致跑到错误分支（常见：写了 `get_strategy_config` 又写了 `if __name__ == '__main__': main()`，结果变成脚本模式，去读不存在的 `/data/pred/pred.csv`）。

### 模式 A — 模块型（模型 / TopK / 平台回测，默认优先）

适用：选股、模型预测、`<PRED>`、RedisTopk / RedisRecording、基本面 `f_` 过滤。

必须：
- 只输出 `get_strategy_config()` 和/或 `STRATEGY_CONFIG`
- `signal` 用 `"<PRED>"`（由平台解析模型目录 `pred.pkl` / 环境变量 `QLIB_PRED_PATH`）

禁止：
- `def main()` / `def run()`
- `if __name__ == "__main__":`
- 顶层直接调用回测 / `print` 跑全流程
- 手写 `pred.csv`、`/data/pred/...`、`path/to/...`
- `pd.read_csv` 读预测文件

### 模式 B — 可执行脚本（传统指标自算收益）

适用：用户明确要求 MACD/KDJ/RSI/BOLL/均线等「脚本直接跑出收益指标」。

必须：
- `def main(): ...` 且文件末尾：
  ```python
  if __name__ == "__main__":
      main()
  ```
- 数据：`qlib.init(provider_uri="{{PROVIDER_URI}}", region="{{MARKET_REGION}}")` + `D.features(...)`
- 收益用 `position = signal.shift(1)`

禁止：
- `get_strategy_config` / `STRATEGY_CONFIG`
- 引用 `RedisTopkStrategy` 等平台策略类
- 硬编码 `/data/pred/pred.csv` 或任意占位 CSV 路径
- 依赖「先有预测文件」才能跑（指标策略从行情算信号，不读 pred）

## 预测文件口径（两种模式通用）

| 正确 | 错误 |
|------|------|
| `"signal": "<PRED>"`（模式 A） | `/data/pred/pred.csv` |
| `os.environ.get("QLIB_PRED_PATH")`（仅脚本且确需本地 pred 时） | `path/to/your/pred.csv` |
| 模型目录下 `pred.pkl` / `pred.parquet` | 虚构的 `/data/pred/` 目录 |

缺预测文件时：提示用户先在「模型管理」对该模型执行推理；不要编造路径。

## 输出前自检（二选一勾选）

- [ ] 模式 A：仅有配置入口，无 `main` / `__main__`
- [ ] 模式 B：有 `main` + `__main__` 守卫，无 `get_strategy_config`
- [ ] 全文无 `/data/pred`、无 `pred.csv` 占位路径
- [ ] 未把模式 A/B 写进同一个文件
