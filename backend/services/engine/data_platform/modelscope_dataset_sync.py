#!/usr/bin/env python3
"""魔搭（ModelScope）QuantDB 数据集 → 本地数据目录一键初始化。

从魔搭公开数据集仓库（默认 ``qusong0627/LightGBM_Alpha300``）分页枚举远端
parquet 清单（含 sha256 / size），并发流式下载到 ``QM_QUANTDB_DATA_DIR``，
逐文件 sha256 校验后原子覆盖，并按需重建同步状态库，使后续
``quantdb_daily_sync`` 的增量 fast-path 直接命中，避免全量重拉。

远端仓库根目录与本地数据集 ``rel_dir`` 一一对应（如
``6_ml_datasets/l1_l2_factors/dt=20260918/data.parquet``），无需映射表。

设计要点：
  - 只依赖 stdlib + httpx（requirements 已含），不引入 modelscope SDK。
  - 幂等可断点：已存在且 size 一致的文件跳过，重跑只补缺失/变更。
  - 下载走 302 → CDN 签名 URL，httpx 自动跟随重定向。
  - 覆盖语义：overwrite=增量覆盖（改动覆盖、缺失补齐、不删无关文件）；
    purge=先删选中数据集目录再全量拉取。
  - 状态库重建：meta=直接用远端 sha256 写 objects（免 56GB 重哈希）；
    rescan=全量重扫（慢，但会校验 parquet magic）；none=不动状态库。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from collections.abc import Callable
from urllib.parse import quote

import httpx

log = logging.getLogger("modelscope_dataset_sync")

DEFAULT_ENDPOINT = "https://www.modelscope.cn"
DEFAULT_REPO_ID = "qusong0627/LightGBM_Alpha300"
DEFAULT_REVISION = "master"

TREE_PAGE_SIZE = 1000
DEFAULT_WORKERS = 6
DOWNLOAD_RETRIES = 3
FILE_PROGRESS_STEP = 25
BATCH_FACTOR = 8  # 每批提交 workers * BATCH_FACTOR 个任务，便于响应取消

_USER_AGENT = "QuantMind-ModelScopeSync/1.0"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def _endpoint() -> str:
    return (os.getenv("MODELSCOPE_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")


def _repo_id() -> str:
    return os.getenv("MODELSCOPE_DATASET_REPO") or DEFAULT_REPO_ID


def _revision() -> str:
    return os.getenv("MODELSCOPE_DATASET_REVISION") or DEFAULT_REVISION


def _token() -> str | None:
    token = os.getenv("MODELSCOPE_TOKEN") or os.getenv("MODELSCOPE_API_TOKEN")
    return token or None


def _workers() -> int:
    try:
        return max(1, int(os.getenv("MODELSCOPE_SYNC_WORKERS", str(DEFAULT_WORKERS))))
    except ValueError:
        return DEFAULT_WORKERS


def resolve_data_root() -> Path:
    """目标数据目录：与 quantdb_daily_sync / 本地扫描同源（QM_QUANTDB_DATA_DIR）。"""
    from backend.scripts.quantdb_local_scan import _default_root

    return Path(os.path.abspath(_default_root()))


# ---------------------------------------------------------------------------
# 远端枚举
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RemoteFile:
    path: str  # 仓库内 posix 相对路径
    size: int
    sha256: str
    dataset: str | None  # 映射到的本地数据集；未知顶层目录为 None
    layout: str  # v2_daily_partition / v1_symbol


def _classify(rel_path: str) -> tuple[str | None, str]:
    """把仓库相对路径归类到数据集。返回 (dataset, layout_col)。"""
    from backend.shared.quantdb_datasets import DATASETS

    for spec in DATASETS:
        if rel_path.startswith(spec.rel_dir + "/"):
            layout = "v2_daily_partition" if spec.layout == "partition" else "v1_symbol"
            return spec.dataset, layout
    return None, "v2_manifest"


def _headers() -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    token = _token()
    if token:
        # 私有仓库 / 提高限流阈值；公开数据集留空即可
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _file_url(endpoint: str, repo_id: str, revision: str, rel_path: str) -> str:
    return (
        f"{endpoint}/api/v1/datasets/{repo_id}/repo"
        f"?Revision={quote(revision, safe='')}&FilePath={quote(rel_path, safe='')}"
    )


def list_remote_files(
    *,
    endpoint: str | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress_cb: Callable[..., None] | None = None,
) -> list[RemoteFile]:
    """分页枚举魔搭仓库全部 blob（递归），返回带 sha256/size 的清单。"""
    ep = (endpoint or _endpoint()).rstrip("/")
    repo = repo_id or _repo_id()
    rev = revision or _revision()
    url = f"{ep}/api/v1/datasets/{repo}/repo/tree"

    files: dict[str, RemoteFile] = {}
    page = 1
    seen_entries = 0
    total: int | None = None
    timeout = httpx.Timeout(30.0, read=120.0)
    with httpx.Client(
        follow_redirects=True, timeout=timeout, headers=_headers()
    ) as client:
        while True:
            if should_cancel is not None and should_cancel():
                break
            resp = client.get(
                url,
                params={
                    "Revision": rev,
                    "Recursive": "true",
                    "PageNumber": page,
                    "PageSize": TREE_PAGE_SIZE,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("Code") != 200:
                raise RuntimeError(
                    f"ModelScope tree API 失败: {body.get('Message') or body.get('Code')}"
                )
            payload = body.get("Data") or {}
            entries = payload.get("Files") or []
            if total is None:
                total = int(payload.get("TotalCount") or 0)
            seen_entries += len(entries)
            for ent in entries:
                if ent.get("Type") != "blob":
                    continue
                raw_path = ent.get("Path") or ent.get("Name") or ""
                if not raw_path:
                    continue
                dataset, layout = _classify(raw_path)
                files[raw_path] = RemoteFile(
                    path=raw_path,
                    size=int(ent.get("Size") or 0),
                    sha256=(ent.get("Sha256") or "").lower(),
                    dataset=dataset,
                    layout=layout,
                )
            if progress_cb:
                progress_cb("enumerate", done=seen_entries, total=total or seen_entries)
            if not entries or (total is not None and seen_entries >= total):
                break
            page += 1
    log.info("[MODELSCOPE] 枚举 %d 个文件（%d 页）", len(files), page)
    return list(files.values())


# ---------------------------------------------------------------------------
# 本地落盘
# ---------------------------------------------------------------------------
def _target_path(root: Path, rel_path: str) -> Path:
    """把仓库相对路径安全地映射到本地绝对路径（防目录穿越）。"""
    pure = PurePosixPath(rel_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"非法远端路径: {rel_path}")
    target = (root / pure).resolve()
    root_resolved = root.resolve()
    if os.path.commonpath([str(root_resolved), str(target)]) != str(root_resolved):
        raise ValueError(f"路径越界: {rel_path}")
    return target


def _download_one(
    client: httpx.Client, url: str, remote: RemoteFile, target: Path
) -> str:
    """下载单个文件：.part → sha256 校验 → 原子覆盖。失败抛异常。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    last_exc: Exception | None = None
    for attempt in range(DOWNLOAD_RETRIES):
        try:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                h = hashlib.sha256()
                size = 0
                with open(part, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        h.update(chunk)
                        size += len(chunk)
            if remote.size and size != remote.size:
                raise OSError(f"size 不符: 期望 {remote.size} 实得 {size}")
            if remote.sha256 and h.hexdigest() != remote.sha256:
                raise OSError("sha256 校验失败")
            os.replace(part, target)
            return "downloaded"
        except Exception as exc:  # noqa: BLE001 - 重试后统一抛出
            last_exc = exc
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            if attempt < DOWNLOAD_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc if last_exc else RuntimeError("下载失败")


def _partition(
    root: Path, files: list[RemoteFile]
) -> tuple[list[RemoteFile], list[RemoteFile]]:
    """按 size 分流为 (已存在, 待下载)。"""
    present: list[RemoteFile] = []
    pending: list[RemoteFile] = []
    for f in files:
        try:
            target = _target_path(root, f.path)
        except ValueError:
            continue
        if target.is_file() and (not f.size or target.stat().st_size == f.size):
            present.append(f)
        else:
            pending.append(f)
    return present, pending


# ---------------------------------------------------------------------------
# 状态库重建
# ---------------------------------------------------------------------------
def _open_state_db(path: Path):
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS objects ("
        "key TEXT PRIMARY KEY, etag TEXT, sha256 TEXT, size INTEGER,"
        " path TEXT, layout TEXT, dataset TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS releases (dataset TEXT PRIMARY KEY, release_id TEXT NOT NULL)"
    )
    return conn


def _write_state_meta(
    root: Path, per_dataset: dict[str, list[RemoteFile]]
) -> tuple[dict[str, str], int]:
    """用远端元数据直接写 objects 表（免重哈希），使增量 fast-path 命中。"""
    from backend.scripts.quantdb_daily_sync import _state_path

    targets: list[tuple[str, Path]] = [
        ("quantmind", _state_path(root)),
        ("sdk", root / "quantdb_sync.sqlite"),
    ]
    written = 0
    for _, db_path in targets:
        conn = _open_state_db(db_path)
        try:
            for dataset, files in per_dataset.items():
                conn.execute("DELETE FROM objects WHERE dataset=?", (dataset,))
                rows = [
                    (
                        f.path,
                        f.sha256,
                        f.sha256,
                        f.size,
                        str(_target_path(root, f.path)),
                        f.layout,
                        dataset,
                    )
                    for f in files
                ]
                conn.executemany(
                    "INSERT OR REPLACE INTO objects(key, etag, sha256, size, path, layout, dataset)"
                    " VALUES(?,?,?,?,?,?,?)",
                    rows,
                )
                written += len(rows)
            conn.commit()
        finally:
            conn.close()
    return {label: str(p) for label, p in targets}, written


def _purge_datasets(root: Path, datasets: list[str]) -> list[str]:
    """删除选中数据集的本地目录（仅限已知 rel_dir，防误删）。"""
    from backend.shared.quantdb_datasets import get_dataset_spec

    removed: list[str] = []
    root_resolved = str(root.resolve())
    for name in datasets:
        spec = get_dataset_spec(name)
        d = (root / spec.rel_dir).resolve()
        if not str(d).startswith(root_resolved):
            continue
        if d.is_dir():
            shutil.rmtree(d)
            removed.append(spec.rel_dir)
    return removed


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _progress(progress_cb: Callable[..., None] | None, event: str, **kw: Any) -> None:
    if progress_cb:
        try:
            progress_cb(event, **kw)
        except Exception:  # noqa: BLE001 - 进度回调异常不得中断同步
            log.debug("progress_cb 异常", exc_info=True)


def _group_remote(
    remote: list[RemoteFile], datasets: list[str] | None
) -> dict[str, list[RemoteFile]]:
    grouped: dict[str, list[RemoteFile]] = {}
    for f in remote:
        if f.dataset is None or not f.path.endswith(".parquet"):
            continue
        if datasets is not None and f.dataset not in datasets:
            continue
        grouped.setdefault(f.dataset, []).append(f)
    return grouped


def init_from_modelscope(
    datasets: list[str] | None = None,
    *,
    mode: str = "overwrite",
    rebuild_state: str = "meta",
    with_pg: bool = False,
    with_qlib: bool = False,
    dry_run: bool = False,
    repo_id: str | None = None,
    revision: str | None = None,
    endpoint: str | None = None,
    progress_cb: Callable[..., None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """从魔搭拉取 QuantDB 数据并覆盖本地目录。

    datasets: 数据集名列表（DATASETS 规格）；None=仓库内可识别的全部。
    mode: overwrite（增量覆盖）| purge（先删选中数据集再全量拉取）。
    rebuild_state: meta | rescan | none。
    """
    from backend.shared.quantdb_datasets import DATASETS

    if mode not in ("overwrite", "purge"):
        raise ValueError(f"未知覆盖模式: {mode}")
    if rebuild_state not in ("meta", "rescan", "none"):
        raise ValueError(f"未知状态库模式: {rebuild_state}")

    known = {spec.dataset for spec in DATASETS}
    if datasets is not None:
        unknown = set(datasets) - known
        if unknown:
            raise ValueError(f"未知数据集: {', '.join(sorted(unknown))}")

    root = resolve_data_root()
    root.mkdir(parents=True, exist_ok=True)
    started = time.time()

    _progress(progress_cb, "phase", phase="enumerate", message="枚举魔搭远端清单")
    remote = list_remote_files(
        endpoint=endpoint,
        repo_id=repo_id,
        revision=revision,
        should_cancel=should_cancel,
        progress_cb=progress_cb,
    )
    grouped = _group_remote(remote, datasets)

    if datasets is not None:
        missing = [d for d in datasets if d not in grouped]
        if missing:
            raise ValueError(
                f"魔搭仓库中不存在这些数据集: {', '.join(sorted(missing))}"
            )

    total_files = sum(len(v) for v in grouped.values())
    total_bytes = sum(f.size for v in grouped.values() for f in v)
    _progress(
        progress_cb,
        "enumerate_done",
        datasets=len(grouped),
        files=total_files,
        bytes=total_bytes,
    )

    if dry_run:
        return {
            "dry_run": True,
            "root": str(root),
            "mode": mode,
            "datasets": {
                k: {"files": len(v), "bytes": sum(f.size for f in v)}
                for k, v in grouped.items()
            },
            "total_files": total_files,
            "total_bytes": total_bytes,
            "elapsed_sec": round(time.time() - started, 1),
        }

    if mode == "purge":
        _progress(progress_cb, "phase", phase="purge", message="清空选中数据集目录")
        _purge_datasets(root, list(grouped))

    workers = _workers()
    batch = max(workers, workers * BATCH_FACTOR)
    timeout = httpx.Timeout(30.0, read=300.0, write=120.0, pool=60.0)

    downloaded = up_to_date = errors = downloaded_bytes = 0
    error_samples: list[str] = []
    per_dataset_result: dict[str, dict[str, Any]] = {}
    cancelled = False
    ep = endpoint or _endpoint()
    repo = repo_id or _repo_id()
    rev = revision or _revision()

    with httpx.Client(
        follow_redirects=True, timeout=timeout, headers=_headers()
    ) as client:
        for idx, (dataset, files) in enumerate(grouped.items()):
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            present, pending = _partition(root, files)
            up_to_date += len(present)
            downloaded_bytes += sum(f.size for f in present)
            _progress(
                progress_cb,
                "dataset_start",
                dataset=dataset,
                index=idx,
                total=len(grouped),
                files=len(files),
                pending=len(pending),
            )
            ds_downloaded = ds_errors = done_in_ds = 0

            for start in range(0, len(pending), batch):
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
                window = pending[start : start + batch]
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            _download_one,
                            client,
                            _file_url(ep, repo, rev, f.path),
                            f,
                            _target_path(root, f.path),
                        ): f
                        for f in window
                    }
                    for fut in as_completed(futures):
                        f = futures[fut]
                        try:
                            fut.result()
                            ds_downloaded += 1
                            downloaded_bytes += f.size
                        except Exception as exc:  # noqa: BLE001
                            ds_errors += 1
                            if len(error_samples) < 20:
                                error_samples.append(f"{f.path}: {exc}")
                            log.warning("[MODELSCOPE] %s 下载失败: %s", f.path, exc)
                        done_in_ds += 1
                        if done_in_ds % FILE_PROGRESS_STEP == 0:
                            _progress(
                                progress_cb,
                                "file",
                                dataset=dataset,
                                done=done_in_ds,
                                total=len(pending),
                                downloaded=downloaded_bytes,
                                errors=errors + ds_errors,
                            )
                if cancelled:
                    break

            downloaded += ds_downloaded
            errors += ds_errors
            per_dataset_result[dataset] = {
                "files": len(files),
                "downloaded": ds_downloaded,
                "up_to_date": len(present),
                "errors": ds_errors,
                "bytes": sum(f.size for f in files),
            }
            _progress(
                progress_cb,
                "dataset_done",
                dataset=dataset,
                downloaded=ds_downloaded,
                up_to_date=len(present),
                errors=ds_errors,
            )
            log.info(
                "[MODELSCOPE] %s: 下载 %d 跳过 %d 失败 %d",
                dataset,
                ds_downloaded,
                len(present),
                ds_errors,
            )
            if cancelled:
                break

    state_info: dict[str, Any] = {"mode": rebuild_state, "status": "skipped"}
    if rebuild_state == "meta" and not cancelled:
        _progress(progress_cb, "phase", phase="state", message="写入同步状态库")
        try:
            per_ds_files = {
                d: [f for f in grouped[d] if _target_path(root, f.path).is_file()]
                for d in grouped
            }
            dbs, written = _write_state_meta(root, per_ds_files)
            state_info = {
                "mode": "meta",
                "status": "ok",
                "objects": written,
                "state_dbs": dbs,
            }
        except Exception as exc:  # noqa: BLE001
            state_info = {"mode": "meta", "status": "failed", "reason": str(exc)}
            log.error("[MODELSCOPE] 状态库写入失败: %s", exc, exc_info=True)
    elif rebuild_state == "rescan" and not cancelled:
        _progress(progress_cb, "phase", phase="state", message="全量重扫建立状态库")
        try:
            from backend.scripts.quantdb_local_scan import scan_local_data

            summary = scan_local_data(
                root=root,
                datasets=list(grouped),
                force=True,
                should_cancel=should_cancel,
            )
            state_info = {
                "mode": "rescan",
                "status": "ok",
                "registered": summary.get("registered"),
                "invalid_files": summary.get("invalid_files"),
                "state_dbs": summary.get("state_dbs"),
            }
        except Exception as exc:  # noqa: BLE001
            state_info = {"mode": "rescan", "status": "failed", "reason": str(exc)}
            log.error("[MODELSCOPE] 状态库重扫失败: %s", exc, exc_info=True)

    pg_info: dict[str, Any] = {"status": "skipped"}
    if with_pg and not cancelled:
        _progress(
            progress_cb, "phase", phase="pg", message="填充 PG stock_daily_latest"
        )
        try:
            from backend.scripts.quantdb_daily_sync import fill_pg_from_parquet

            pg_info = {"status": "ok", "result": fill_pg_from_parquet()}
        except Exception as exc:  # noqa: BLE001
            pg_info = {"status": "failed", "reason": str(exc)}
            log.error("[MODELSCOPE] PG 填充失败: %s", exc, exc_info=True)

    qlib_info: dict[str, Any] = {"status": "skipped"}
    if with_qlib and not cancelled:
        _progress(progress_cb, "phase", phase="qlib", message="重建 Qlib 缓存")
        try:
            from backend.scripts.quantdb_daily_sync import update_qlib_cache

            qlib_info = {"status": "ok", "provider_uri": update_qlib_cache()}
        except Exception as exc:  # noqa: BLE001
            qlib_info = {"status": "failed", "reason": str(exc)}
            log.error("[MODELSCOPE] Qlib 重建失败: %s", exc, exc_info=True)

    return {
        "root": str(root),
        "repo_id": repo,
        "mode": mode,
        "cancelled": cancelled,
        "datasets": per_dataset_result,
        "total_files": total_files,
        "downloaded": downloaded,
        "up_to_date": up_to_date,
        "errors": errors,
        "downloaded_bytes": downloaded_bytes,
        "error_samples": error_samples,
        "state": state_info,
        "pg": pg_info,
        "qlib": qlib_info,
        "elapsed_sec": round(time.time() - started, 1),
    }


def preflight_modelscope(
    datasets: list[str] | None = None,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """初始化前预检：远端各数据集文件数/字节、本地目录与磁盘余量。"""
    from backend.shared.quantdb_datasets import DATASETS

    root = resolve_data_root()
    root.mkdir(parents=True, exist_ok=True)

    remote = list_remote_files(endpoint=endpoint, repo_id=repo_id, revision=revision)
    grouped = _group_remote(remote, datasets)

    by_name = {spec.dataset: spec for spec in DATASETS}
    items = []
    for name, files in sorted(grouped.items()):
        spec = by_name.get(name)
        items.append(
            {
                "dataset": name,
                "name": spec.name if spec else name,
                "group": spec.group if spec else "",
                "layout": spec.layout if spec else "partition",
                "rel_dir": spec.rel_dir if spec else "",
                "files": len(files),
                "bytes": sum(f.size for f in files),
            }
        )

    try:
        usage = shutil.disk_usage(str(root))
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        disk = {"total": 0, "used": 0, "free": 0}

    total_bytes = sum(it["bytes"] for it in items)
    warnings: list[str] = []
    if disk["free"] and total_bytes and disk["free"] < total_bytes * 1.1:
        warnings.append(
            f"磁盘余量不足：需要约 {total_bytes / 1024**3:.1f} GB，"
            f"当前可用 {disk['free'] / 1024**3:.1f} GB"
        )

    return {
        "repo_id": repo_id or _repo_id(),
        "revision": revision or _revision(),
        "root": str(root),
        "datasets": items,
        "total_files": sum(it["files"] for it in items),
        "total_bytes": total_bytes,
        "disk": disk,
        "warnings": warnings,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
