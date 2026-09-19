# SKILL.md 新建/改版模板（自研技能用；第三方原样引入的 skill 不用套）

```markdown
---
name: <目录同名>
description: "<一句话功能>。在 QuantBot / Claude Code 中<场景>时使用。触发词：<词1、词2>"
---

> ⚙️ 本技能遵循公共运行环境契约（最高优先级，先于本文其余内容执行）：
> 详见 [_shared/env-contract.md](../_shared/env-contract.md)，执行前先读它。

# <标题>

<一句话：解决什么问题，默认参数/口径>

## 认证

```bash
BASE=http://127.0.0.1:8000
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123","tenant_id":"default"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"
```

## 1. <功能分区>

按「认证 → 端点 → 示例」组织，所有 API 统一走 `/api/v1` 前缀，
股票代码用前缀式（如 `SH600036`），涉及市场的操作标注市场参数（CN/HK/US/CRYPTO/FUTURES）。

```bash
curl -s -H "$AUTH" "$BASE/api/v1/<域>/<资源>"
```

## 2. 实战流程（推荐）

用户说"<触发语>"时按哪几步走，1-2-3 列清楚。

## 3. 相关技能

- **[[other-skill]]** — 一句话说明分工，避免功能重叠。

## 4. 常见问题

| 现象 | 处理 |
|---|---|
| ... | ... |
```

目录规范：`skills/<name>/SKILL.md` 必备；可执行脚本放 `scripts/`（重依赖脚本注明必须进 quantmind 容器跑）；方法论放 `references/`（全小写）；需要单测的脚本配 `scripts/tests/`。新增后更新 `skills/README.md` 总览表，跑 `python scripts/lint_skills.py` 通过才能提交。
