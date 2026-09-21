#!/usr/bin/env python3
"""把 Gitea master 的提交列表发布到 release-index 分支。

只在维护端、向 master 推送之后运行。客户服务器禁止执行。
计数以客户会拉到的 Gitea master 为准，不读取 GitHub / Gitee 镜像。

    python scripts/publish_release_index.py
    python scripts/publish_release_index.py --push

默认只 fetch 并打印 JSON。--push 会强制更新远端分支 release-index
（只动这一支，不改 master）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.shared.version import build_release_index  # noqa: E402

_BRANCH = "release-index"
_FILE = "release-index.json"


def _git(*args: str, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        input=input_text,
        capture_output=True,
    )
    return completed.stdout.strip()


def _remote_url(remote: str, fallback: str) -> str:
    try:
        return _git("remote", "get-url", remote)
    except subprocess.CalledProcessError:
        return fallback


def build_from_remote(remote: str, ref: str, repo_url: str) -> dict:
    _git("fetch", remote, ref)
    commits = [
        line.strip()
        for line in _git("rev-list", f"{remote}/{ref}").splitlines()
        if line.strip()
    ]
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%dT%H:%M:%S+08:00"
    )
    return build_release_index(
        repo=repo_url,
        branch=ref,
        commits=commits,
        generated_at=generated_at,
    )


def push_index(remote: str, payload: dict) -> str:
    """用孤儿提交强制更新 release-index，不改动当前工作区。"""
    tmp = ROOT / ".release-index.json.tmp"
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        blob = _git("hash-object", "-w", "--path", _FILE, str(tmp))
    finally:
        tmp.unlink(missing_ok=True)
    tree = _git("mktree", input_text=f"100644 blob {blob}\t{_FILE}\n")
    commit = _git("commit-tree", tree, "-m", "refresh release index")
    _git("push", "--force", remote, f"{commit}:refs/heads/{_BRANCH}")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 Gitea release-index")
    parser.add_argument("--remote", default="gitea")
    parser.add_argument("--ref", default="master")
    parser.add_argument(
        "--repo-url",
        default="",
        help="写入 JSON 的仓库绝对地址，默认读取 git remote",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="强制更新远端 release-index 分支",
    )
    args = parser.parse_args()
    repo_url = args.repo_url or _remote_url(
        args.remote,
        "https://quantmindai.cn/gitea/qusong0627/QuantMind.git",
    )
    payload = build_from_remote(args.remote, args.ref, repo_url)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    print(
        f"commits={payload['rev_count']} head={payload['head']}",
        file=sys.stderr,
    )
    if args.push:
        commit = push_index(args.remote, payload)
        print(f"pushed {_BRANCH} {commit}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
