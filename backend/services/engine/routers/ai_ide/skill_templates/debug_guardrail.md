用途：当用户提供报错日志时，优先做「最小修复」，并遵守入口契约（见 entrypoint_contract）。

诊断流程：
1) 先识别报错类型（ImportError/ModuleNotFoundError/FileNotFoundError/KeyError/NameError 等）。
2) 明确指出根因行与修复方案，不给泛化建议。
3) 禁止新增无关依赖，禁止大规模重构。
4) 修复后保持原策略意图不变，并给可运行版本。
5) **先判断当前代码是模式 A（配置）还是模式 B（脚本）**，只在该模式下修复；不要为了「补 main」而把模块型策略改成脚本。

常见修复规则：
- FileNotFoundError 且路径含 `pred` / `/data/pred`：删掉手写预测路径；模式 A 改 `"signal": "<PRED>"`；模式 B 改用 `D.features` 算信号，不要读 pred。
- FileNotFoundError 其它：移除占位路径，改为 qlib + D.features 或加路径存在性判断。
- ImportError: 删除不存在的导入，保留最小必要 import。
- 链式赋值告警: 改为 .loc 赋值。
- 收益计算异常: 使用 position.shift(1)、对零波动夏普返回 0。

专项错误护栏（高频）：
1) `NameError: name 'qlib' is not defined`
   - 根因：调用 `qlib.init(...)` 但未 `import qlib`
   - 修复：补充 `import qlib`，并删除未使用导入
2) `ImportError: cannot import name 'backtest' from qlib.contrib.evaluate`
   - 根因：版本不兼容或错误导入
   - 修复：删除该导入，若仅做简易回测，使用 pandas 本地回测逻辑
3) `{{PROVIDER_URI}}/*.csv` 或 `/data/pred/pred.csv`
   - 根因：把预测/Qlib 目录误当 CSV
   - 修复：模式 A → `"<PRED>"`；模式 B → `qlib.init + D.features`
4) 同时存在 `get_strategy_config` 与 `main`/`__main__`
   - 根因：入口混用；运行器会优先跑脚本分支导致异常退出
   - 修复：模型策略只保留配置入口，删掉 `main` 与 `__main__` 守卫

输出前强制自检（按模式勾选，不要两条都勾）：
### 若为模式 A（模块型）
- 仅有 `get_strategy_config` / `STRATEGY_CONFIG`
- 无 `def main`、无 `if __name__ == "__main__"`
- `signal` 为 `"<PRED>"` 或合法表达式，无手写 pred 路径

### 若为模式 B（脚本型）
- 已包含 `import qlib` 与 `main()` + `__main__` 守卫
- 不含 `get_strategy_config` / `STRATEGY_CONFIG`
- 不含 `path/to/your/data.csv`、`/data/pred`、`pred.csv` 占位
- 不含 `from qlib.contrib.evaluate import backtest`
- 收益计算使用 `position = signal.shift(1)`
