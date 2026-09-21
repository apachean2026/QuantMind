"""backend/shared/version.py 单元测试。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

import backend.shared.version as vmod


def test_version_falls_back_to_dev(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vmod, "_VERSION_JSON", tmp_path / "missing.json")
    monkeypatch.setattr(vmod, "_VERSION_TXT", tmp_path / "nonexistent.txt")
    assert vmod.get_version_info()["version"] == "dev"
    assert vmod.get_version_info()["commit"] == ""


def test_version_reads_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vmod, "_VERSION_JSON", tmp_path / "missing.json")
    vtxt = tmp_path / "version.txt"
    vtxt.write_text("v1.10.0\n", encoding="utf-8")
    monkeypatch.setattr(vmod, "_VERSION_TXT", vtxt)
    assert vmod.get_version_info()["version"] == "v1.10.0"


def test_version_ignores_blank(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vmod, "_VERSION_JSON", tmp_path / "missing.json")
    vtxt = tmp_path / "version.txt"
    vtxt.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(vmod, "_VERSION_TXT", vtxt)
    assert vmod.get_version_info()["version"] == "dev"


def test_version_reads_json_commit(tmp_path: Path, monkeypatch):
    vjson = tmp_path / "version.json"
    vjson.write_text(
        json.dumps(
            {
                "version": "v1.2.0",
                "commit": "abc123",
                "branch": "master",
                "rev_count": 10,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vmod, "_VERSION_JSON", vjson)
    info = vmod.get_version_info()
    assert info["commit"] == "abc123"
    assert info["version"] == "v1.2.0"


def test_build_release_index_head_is_newest():
    payload = vmod.build_release_index(
        repo="https://example/repo.git",
        branch="master",
        commits=["aaa", "bbb", "ccc"],
        generated_at="2026-09-21T22:00:00+08:00",
    )
    assert payload["head"] == "aaa"
    assert payload["rev_count"] == 3
    assert payload["commits"][0] == "aaa"


def test_compute_behind_at_head():
    index = {"head": "aaa", "branch": "master", "commits": ["aaa", "bbb"]}
    result = vmod.compute_behind("aaa", index)
    assert result is not None
    assert result["behind"] == 0
    assert result["status"] == "ok"
    assert result["is_up_to_date"] is True


def test_compute_behind_counts_index():
    index = {"head": "AAA", "branch": "master", "commits": ["aaa", "bbb", "ccc"]}
    result = vmod.compute_behind("CCC", index)
    assert result is not None
    assert result["behind"] == 2
    assert result["status"] == "ok"
    assert result["is_up_to_date"] is False


def test_compute_behind_diverged_when_sha_missing():
    index = {"head": "aaa", "branch": "master", "commits": ["aaa", "bbb"]}
    result = vmod.compute_behind("zzz", index)
    assert result is not None
    assert result["behind"] is None
    assert result["status"] == "diverged"
    assert result["is_up_to_date"] is False


def test_compute_behind_rejects_malformed_index():
    assert vmod.compute_behind("aaa", {"head": "aaa"}) is None


def test_check_updates_fetches_index(tmp_path: Path, monkeypatch):
    vjson = tmp_path / "version.json"
    vjson.write_text(
        json.dumps({"version": "v1", "commit": "bbb", "branch": "master"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vmod, "_VERSION_JSON", vjson)
    monkeypatch.setattr(vmod, "_VERSION_TXT", tmp_path / "missing.txt")
    monkeypatch.setattr(vmod, "_CACHE_FILE", tmp_path / "cache.json")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "head": "aaa",
                "branch": "master",
                "commits": ["aaa", "bbb", "ccc"],
            }

    monkeypatch.setattr(vmod._httpx, "get", lambda url: _Resp())
    result = asyncio.run(vmod.check_updates(force=True))
    assert result is not None
    assert result["behind"] == 1
    assert result["status"] == "ok"
    assert result["upstream_head"] == "aaa"
    cached = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert cached["behind"] == 1


def test_check_updates_remote_failure_returns_none(tmp_path: Path, monkeypatch):
    vjson = tmp_path / "version.json"
    vjson.write_text(
        json.dumps({"version": "v1", "commit": "bbb", "branch": "master"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vmod, "_VERSION_JSON", vjson)
    monkeypatch.setattr(vmod, "_CACHE_FILE", tmp_path / "cache.json")

    def _boom(url):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(vmod._httpx, "get", _boom)
    assert asyncio.run(vmod.check_updates(force=True)) is None
    assert not (tmp_path / "cache.json").exists()


def test_check_updates_ignores_legacy_cache(tmp_path: Path, monkeypatch):
    vjson = tmp_path / "version.json"
    vjson.write_text(
        json.dumps({"version": "v1", "commit": "bbb", "branch": "master"}),
        encoding="utf-8",
    )
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                "behind": 10,
                "behind_capped": False,
                "upstream_branch": "master",
                "checked_at": int(__import__("time").time()),
                "is_up_to_date": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vmod, "_VERSION_JSON", vjson)
    monkeypatch.setattr(vmod, "_VERSION_TXT", tmp_path / "missing.txt")
    monkeypatch.setattr(vmod, "_CACHE_FILE", cache)

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "head": "bbb",
                "branch": "master",
                "commits": ["bbb", "aaa"],
            }

    monkeypatch.setattr(vmod._httpx, "get", lambda url: _Resp())
    result = asyncio.run(vmod.check_updates(force=False))
    assert result is not None
    assert result["behind"] == 0
    assert result["status"] == "ok"
    assert result["local_commit"] == "bbb"


def test_check_updates_skips_without_commit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vmod, "_VERSION_JSON", tmp_path / "missing.json")
    monkeypatch.setattr(vmod, "_VERSION_TXT", tmp_path / "missing.txt")
    called = {"n": 0}

    def _get(url):
        called["n"] += 1
        raise AssertionError("should not fetch")

    monkeypatch.setattr(vmod._httpx, "get", _get)
    assert asyncio.run(vmod.check_updates(force=True)) is None
    assert called["n"] == 0
