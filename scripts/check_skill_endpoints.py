#!/usr/bin/env python3
"""技能端点漂移核验：技能引用的 API 路径 vs 后端真实路由。

原理：
  1. AST 解析 backend 各服务 main.py 的 include_router(X, prefix=...) + import，
     得到 模块 -> 挂载前缀；
  2. AST 解析全部路由文件：APIRouter(prefix) 定义、include_router 子路由、
     @router.get/post/... 装饰器，拼接出 (method, full_template) 全量路由表；
  3. 从 skills/*/SKILL.md 提取引用的 /api/v1/... 路径（归一化 {param}），
     与路由表做模板匹配，输出漂移报告。

用法：python scripts/check_skill_endpoints.py [--report PATH]
"""
import ast
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"

SERVICES = [
    "backend/services/api",
    "backend/services/engine",
    "backend/services/trade",
    "backend/services/stream",
    "backend/services/simulation",
]
MAIN_FILES = [
    "backend/services/api/main.py",
    "backend/services/engine/main.py",
    "backend/services/trade/main.py",
    "backend/services/stream/main.py",
    "backend/services/engine/data_gateway/main.py",
]

SKILL_PATH_RE = re.compile(r"/api/v1/[A-Za-z0-9_\-/{}.$]+")
WS_PATH_RE = re.compile(r"(?:\"|'/ws|/ws\b)[A-Za-z0-9_\-/{}]*")


def mod_of(path: pathlib.Path) -> str:
    return path.relative_to(REPO).with_suffix("").as_posix().replace("/", ".")


class FileInfo:
    def __init__(self):
        self.routers: dict[str, str] = {}   # var -> own prefix
        # (parent_var, imp_mod, imp_attr, sub_attr, extra_prefix)
        #  Attribute 式：auth.router -> (app, M, auth, router)；Name 式：X -> (app, M, X, "")
        self.includes: list[tuple[str, str, str, str, str]] = []
        self.routes: list[tuple[str, str, str]] = []    # (var, method, path)
        self.imports: dict[str, tuple[str, str]] = {}   # local name -> (module, attr)


def resolve_child(files: dict[str, "FileInfo"], mod: str, attr: str) -> str:
    """把 (模块, 属性) 解析为路由文件模块名：属性可能是子模块也可能是对象。"""
    if mod + "." + attr in files:
        return mod + "." + attr  # from routers import auth + auth.router
    if mod in files:
        return mod  # from X import router (+ router.X)
    return ""


def parse_file(path: pathlib.Path) -> FileInfo:
    info = FileInfo()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return info
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                local = a.asname or a.name
                # local -> (模块, 属性)：属性可能是子模块（如 auth）也可能是对象（如 router）
                info.imports[local] = (node.module, a.name)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            val = node.value if isinstance(node, ast.Assign) else node.value
            if isinstance(val, ast.Call) and getattr(val.func, "attr", "") == "APIRouter":
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                prefix = ""
                for kw in val.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value or ""
                for t in targets:
                    if isinstance(t, ast.Name):
                        info.routers[t.id] = prefix
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr == "include_router":
                # app.include_router(X, prefix=...) 或 router.include_router(X)
                parent = fn.value.id if isinstance(fn.value, ast.Name) else (
                    fn.value.attr if isinstance(fn.value, ast.Attribute) else "?")
                if not call.args:
                    continue
                arg = call.args[0]
                extra = ""
                for kw in call.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        extra = kw.value.value or ""
                if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                    # auth.router / X.router：原样记录 (base_mod, base_attr, sub_attr)，二阶段再解析
                    base = arg.value.id
                    imp = info.imports.get(base, ("", ""))
                    info.includes.append((parent, imp[0], imp[1], arg.attr, extra))
                elif isinstance(arg, ast.Name):
                    imp = info.imports.get(arg.id, ("", ""))
                    info.includes.append((parent, imp[0], imp[1], "", extra))
                # Call 型（如 build_per_model_router(...)）跳过，靠兜底补
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    m = dec.func.attr
                    if m in ("get", "post", "put", "delete", "patch", "websocket", "api_route"):
                        if isinstance(dec.func.value, ast.Name) and dec.args:
                            a0 = dec.args[0]
                            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                                info.routes.append((dec.func.value.id, m.upper(), a0.value))
    return info


def main() -> int:
    report_path = pathlib.Path(sys.argv[sys.argv.index("--report") + 1]) \
        if "--report" in sys.argv else REPO / "skills" / "_shared" / "drift_report.md"

    files: dict[str, FileInfo] = {}
    for svc in SERVICES:
        for p in (REPO / svc).rglob("*.py"):
            files[mod_of(p)] = parse_file(p)

    # 挂载前缀：main 文件的 includes 中 parent 为 app
    mounts: dict[tuple[str, str], str] = {}  # (child_mod, child_var) -> mount prefix
    for main in MAIN_FILES:
        info = files.get(mod_of(REPO / main))
        if not info:
            continue
        for parent, cmod, cvar, extra in info.includes:
            if parent == "app" and cmod:
                mounts.setdefault((cmod, cvar), extra)

    # 子路由链：(mod,var) -> list of (parent_mod, parent_var, extra)
    parents: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for mod, info in files.items():
        for parent_var, cmod, cvar, extra in info.includes:
            if parent_var == "app" or not cmod:
                continue
            # 子模块可能是包内相对名；做后缀匹配兜底
            cands = [m for m in files if m == cmod or m.endswith("." + cmod.split(".")[-1])
                     and cmod.split(".")[-1] in m]
            target_mod = cmod if cmod in files else (cands[0] if cands else cmod)
            parents.setdefault((target_mod, cvar), []).append((mod, parent_var, extra))

    sys.setrecursionlimit(10000)
    memo: dict[tuple[str, str], list[str]] = {}

    def chains(mod: str, var: str, depth: int = 0) -> list[str]:
        key = (mod, var)
        if key in memo:
            return memo[key]
        if depth > 8:
            return [""]
        info = files.get(mod)
        own = (info.routers.get(var, "") if info else "")
        out = [own]
        for pmod, pvar, extra in parents.get(key, []):
            for pc in chains(pmod, pvar, depth + 1):
                out.append(pc + extra + own)
        memo[key] = out
        return out

    backend_routes: set[tuple[str, str]] = set()
    for mod, info in files.items():
        for var, method, path in info.routes:
            if not path.startswith("/"):
                continue
            for ch in chains(mod, var):
                mount = mounts.get((mod, var), "")
                if not mount:
                    # 沿父链找挂载
                    found = ""
                    seen = set()

                    def find_mount(m: str, v: str, d: int = 0) -> str:
                        if d > 8 or (m, v) in seen:
                            return ""
                        seen.add((m, v))
                        if (m, v) in mounts:
                            return mounts[(m, v)]
                        for pm, pv, _ in parents.get((m, v), []):
                            r = find_mount(pm, pv, d + 1)
                            if r:
                                return r
                        return ""
                    found = find_mount(mod, var)
                    mount = found
                full = (mount + ch + path).replace("//", "/")
                backend_routes.add((method, full))

    def normalize(p: str) -> str:
        p = p.split("?")[0].rstrip("/") or "/"
        return re.sub(r"\{[^}]*\}", "{}", p)

    backend_norm = {(m, normalize(p)) for m, p in backend_routes}

    def match(path: str) -> bool:
        n = normalize(path)
        segs = n.split("/")
        for m, bp in backend_norm:
            bs = bp.split("/")
            if len(bs) != len(segs):
                continue
            if all(a == "{}" or a == b for a, b in zip(bs, segs)):
                return True
        return False

    # 技能侧提取
    skill_hits: dict[str, list[str]] = {}
    for d in sorted((REPO / "skills").iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")) or not (d / "SKILL.md").exists():
            continue
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        paths = set()
        for m in SKILL_PATH_RE.finditer(text):
            p = m.group(0).rstrip(".,);\"'").rstrip("/")
            if "{path" in p or p.endswith("/{p"):
                continue
            paths.add(p)
        skill_hits[d.name] = sorted(paths)

    dead: dict[str, list[str]] = {}
    for skill, paths in skill_hits.items():
        for p in paths:
            if not match(p):
                dead.setdefault(skill, []).append(p)

    total_refs = sum(len(v) for v in skill_hits.values())
    total_dead = sum(len(v) for v in dead.values())
    print(f"后端路由模板数：{len(backend_norm)}  技能引用路径数：{total_refs}  疑似死链：{total_dead}")
    for skill, paths in sorted(dead.items()):
        print(f"\n## {skill} ({len(paths)})")
        for p in paths:
            print(f"  DEAD {p}")

    lines = ["# 技能端点漂移报告（自动生成，勿手改）",
             "", f"后端路由模板：{len(backend_norm)}；技能引用：{total_refs}；疑似死链：{total_dead}。", ""]
    if dead:
        for skill, paths in sorted(dead.items()):
            lines.append(f"## {skill}（{len(paths)}）")
            for p in paths:
                lines.append(f"- [ ] `{p}`")
            lines.append("")
    else:
        lines.append("无疑似死链。")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
