#!/usr/bin/env python3
"""默认模型推理缺口补全（与前端「一键补全至最新」同链路）。

数据同步窗口默认约 01:00–06:00；本脚本建议在 06:30 后跑：
扫描所有用户 is_default 模型，按 pred.parquet 覆盖算缺口（含历史中间空洞），
逐日 InferenceScriptRunner 补到 QuantDB 因子已产出日。

用法:
  # 全量默认模型补全
  python backend/scripts/backfill_default_inference.py

  # 只看缺口不执行
  python backend/scripts/backfill_default_inference.py --dry-run

  # 限定用户 / 模型
  python backend/scripts/backfill_default_inference.py --user-id <uid>
  python backend/scripts/backfill_default_inference.py --model-id <mid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_default_inference")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="补全所有用户默认模型的推理缺口（含历史空洞）"
    )
    parser.add_argument("--tenant-id", default=None, help="仅处理该租户")
    parser.add_argument("--user-id", default=None, help="仅处理该用户")
    parser.add_argument("--model-id", default=None, help="仅处理该模型")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描缺口，不执行推理",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="将汇总以 JSON 打印到 stdout",
    )
    args = parser.parse_args()

    from backend.services.engine.inference.gap_backfill import (
        backfill_all_default_models,
    )

    summary = asyncio.run(
        backfill_all_default_models(
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            model_id=args.model_id,
            dry_run=args.dry_run,
        )
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    else:
        log.info(
            "完成 status=%s models=%d skipped=%d completed=%d partial=%d failed=%d",
            summary.get("status"),
            summary.get("total_models"),
            summary.get("skipped"),
            summary.get("completed"),
            summary.get("partial"),
            summary.get("failed"),
        )
        for d in summary.get("details") or []:
            log.info(
                "  %s/%s %s status=%s gap=%s appended=%s failed=%s",
                d.get("tenant_id"),
                d.get("user_id"),
                d.get("model_id"),
                d.get("status"),
                d.get("gap"),
                d.get("appended"),
                d.get("failed"),
            )

    status = str(summary.get("status") or "")
    if status == "failed":
        return 2
    if status == "partial":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
