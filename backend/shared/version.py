"""运行时代码版本读取与上游更新检查。

版本来源（优先级）：
1. backend/shared/version.json —— 由 deploy/update.sh 写入
   （version / commit / branch / rev_count，commit 为完整 HEAD SHA）
2. backend/shared/version.txt —— 旧的 describe 单行（遗留回退）
3. 缺省回退 "dev"（本地未走 update.sh 的开发环境）

更新检查：容器内没有 .git，禁止在运行中的后端里 git fetch。
后端读取 Gitea release-index 分支上的 release-index.json，
用本机 commit 在 commits（新→旧）中的下标作为落后提交数。
SHA 不在列表中则 status=diverged，不估算个数。
结果缓存在本地磁盘，避免每次请求都访问上游。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx

_BASE_DIR = Path(__file__).resolve().parent
_VERSION_TXT = _BASE_DIR / "version.txt"
_VERSION_JSON = _BASE_DIR / "version.json"

# 客户部署对比的是 Gitea master 的发布索引，不是 GitHub/Gitee 镜像。
_UPSTREAM_BRANCH = os.getenv("QUANTMIND_UPSTREAM_BRANCH", "master")
_RELEASE_INDEX_URL = os.getenv(
    "QUANTMIND_RELEASE_INDEX_URL",
    "https://quantmindai.cn/gitea/qusong0627/QuantMind/raw/branch/"
    "release-index/release-index.json",
)

# 检查结果缓存在运行时可写目录（STORAGE_ROOT 默认 /data，挂载持久化）。
_CACHE_TTL = int(os.getenv("QUANTMIND_UPDATE_CHECK_TTL", str(6 * 3600)))
_CACHE_FILE = Path(
    os.getenv(
        "QUANTMIND_UPDATE_CACHE_FILE",
        os.path.join(os.getenv("STORAGE_ROOT", "/data"), "version_check.json"),
    )
)
_TIMEOUT = float(os.getenv("QUANTMIND_UPDATE_CHECK_TIMEOUT", "10"))

_httpx = httpx.Client(timeout=_TIMEOUT)
_lock = asyncio.Lock()


def _norm_sha(value: object) -> str:
    return str(value or "").strip().lower()


def _commit_from_describe(describe: str) -> str | None:
    """从 describe（如 v1.9.0-beta-629-g13e38771）解析提交 SHA；恰为 tag 时无 g 段。"""
    match = re.search(r"-g([0-9a-f]{7,40})$", describe.strip())
    return match.group(1) if match else None


def build_release_index(
    *,
    repo: str,
    branch: str,
    commits: list[str],
    generated_at: str,
) -> dict:
    """由 git rev-list（新→旧）生成发布索引。head 必须是列表第一项。"""
    cleaned = [item.strip() for item in commits if item and item.strip()]
    if not cleaned:
        raise ValueError("release index requires at least one commit")
    return {
        "schema": 1,
        "repo": repo,
        "branch": branch,
        "head": cleaned[0],
        "rev_count": len(cleaned),
        "commits": cleaned,
        "generated_at": generated_at,
    }


def compute_behind(local_commit: str, index: dict) -> dict | None:
    """用本机 SHA 在上游索引中的位置计算落后数。

    索引缺 head/commits 时返回 None（调用方按检查失败处理，不展示数字）。
    SHA 不在列表中时 status=diverged，behind 为空。
    """
    if not isinstance(index, dict):
        return None
    head_raw = index.get("head")
    commits_raw = index.get("commits")
    if not isinstance(head_raw, str) or not head_raw.strip():
        return None
    if not isinstance(commits_raw, list):
        return None

    head = _norm_sha(head_raw)
    commits = [item for item in (_norm_sha(c) for c in commits_raw) if item]
    local = _norm_sha(local_commit)
    if not local:
        return None

    branch = str(index.get("branch") or _UPSTREAM_BRANCH)
    if local == head:
        behind: int | None = 0
        status = "ok"
    elif local in commits:
        behind = commits.index(local)
        status = "ok"
    else:
        behind = None
        status = "diverged"
    return {
        "behind": behind,
        "status": status,
        "upstream_branch": branch,
        "upstream_head": head,
        "is_up_to_date": behind == 0,
    }


def get_version_info() -> dict:
    """返回当前部署版本明细：version（describe）、commit（SHA）、branch。"""
    if _VERSION_JSON.is_file():
        try:
            data = json.loads(_VERSION_JSON.read_text(encoding="utf-8"))
            commit = data.get("commit") or ""
            branch = data.get("branch")
            version = data.get("version") or ""
            return {
                "version": version,
                "commit": commit,
                "branch": branch or _UPSTREAM_BRANCH,
            }
        except (OSError, ValueError):
            pass
    try:
        describe = _VERSION_TXT.read_text(encoding="utf-8").strip()
    except OSError:
        describe = ""
    if not describe:
        return {"version": "dev", "commit": "", "branch": _UPSTREAM_BRANCH}
    return {
        "version": describe,
        "commit": _commit_from_describe(describe) or "",
        "branch": _UPSTREAM_BRANCH,
    }


def _load_cache():
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_cache(payload: dict) -> None:
    try:
        Path(_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _fetch_release_index() -> dict:
    resp = _httpx.get(_RELEASE_INDEX_URL)
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("release index is not an object")
    return body


async def check_updates(force: bool = False) -> dict | None:
    """读取 Gitea 发布索引，返回本部署落后的提交数。失败时返回 None。

    返回字段：behind、status、upstream_branch、upstream_head、
    checked_at、is_up_to_date。
    status=ok 时 behind 为下标；status=diverged 时 behind 为 null。
    """
    info = get_version_info()
    commit = info.get("commit")
    if not commit:
        return None

    cache = _load_cache()
    if not force and cache and time.time() - cache.get("checked_at", 0) < _CACHE_TTL:
        return cache

    async with _lock:
        cache = _load_cache()
        if (
            not force
            and cache
            and time.time() - cache.get("checked_at", 0) < _CACHE_TTL
        ):
            return cache

        try:
            index = await asyncio.to_thread(_fetch_release_index)
        except (httpx.HTTPError, ValueError, OSError):
            return None

        result = compute_behind(commit, index)
        if result is None:
            return None
        result["checked_at"] = int(time.time())
        _save_cache(result)
        return result
