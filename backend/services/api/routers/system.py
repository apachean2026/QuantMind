from fastapi import APIRouter
from config.settings import settings
from backend.shared.version import get_version_info, check_updates

router = APIRouter(prefix="/api/v1/system", tags=["System"])


@router.get("/version")
async def system_version(force: bool = False):
    """当前运行代码版本与上游更新检查。

    - version/commit/branch：由 deploy/update.sh 写入 version.json（build 时拷入镜像）。
    - update：读取 Gitea release-index.json，用本机 commit 在提交列表中的
      下标算出落后数。容器无外网、索引不可用或未走 update.sh 时省略；
      force=true 可绕过缓存强制刷新。更新检查失败不影响版本读取。
    """
    info = get_version_info()
    try:
        update = await check_updates(force=force)
    except Exception:
        update = None
    return {
        "version": info["version"],
        "edition": settings.edition,
        "commit": info["commit"],
        "branch": info["branch"],
        "update": update,
    }


@router.get("/capabilities")
async def get_capabilities():
    """获取当前版本的系统能力与开关"""
    return settings.capabilities
