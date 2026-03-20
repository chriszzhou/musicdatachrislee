#!/usr/bin/env python3
"""
从仓库根目录启动 Web，无需事先 pip install -e .（会把 src 加入 sys.path）。

用法（在仓库根目录执行）:
  python3.9 run_web.py --reload   # 系统默认 python3 较旧时，请显式指定 3.9+
  python3 run_web.py --reload     # 若 python3 已是 3.9+ 可直接用

--reload 子进程会再次执行本脚本，因此路径始终正确。

需要 Python 3.9+（与项目 pyproject 一致）。
"""
import argparse
import os
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.stderr.write(
        "需要 Python 3.9+，当前为 {}.{}。\n"
        "请执行: python3.9 run_web.py …\n".format(
            sys.version_info.major,
            sys.version_info.minor,
        )
    )
    raise SystemExit(1)

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if not _SRC.is_dir():
    sys.exit("未找到目录 {!r}，请在 qqmusic-crawler 仓库根目录运行本脚本。".format(str(_SRC)))
_src_str = str(_SRC)
if _src_str not in sys.path:
    sys.path.insert(0, _src_str)

# uvicorn --reload 用 spawn 起子进程时，子进程会继承 PYTHONPATH；仅改 sys.path 可能不够
_old_pp = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = (
    _src_str if not _old_pp else _src_str + os.pathsep + _old_pp
)


def _require_deps() -> None:
    """尽早提示：当前解释器是否已安装项目依赖（与能否 import web_main 一致）。"""
    missing = []
    for mod in ("loguru", "fastapi", "uvicorn"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.stderr.write(
            "当前 Python 未安装项目依赖（缺少: {}）。\n"
            "在仓库根目录用**同一解释器**安装：\n\n"
            "  {!r} -m pip install -e .\n\n"
            "（会安装 pyproject.toml 中的依赖，含 loguru / fastapi / uvicorn 等）\n".format(
                ", ".join(missing),
                sys.executable,
            )
        )
        raise SystemExit(1)


_require_deps()


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 qqmusic_crawler Web (FastAPI)")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址")
    parser.add_argument("--port", type=int, default=8000, help="端口")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    args = parser.parse_args()

    import uvicorn

    reload_dirs = [
        str(_SRC / "qqmusic_crawler"),
        str(_ROOT / "templates"),
    ]
    uvicorn.run(
        "qqmusic_crawler.web_main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[p for p in reload_dirs if Path(p).is_dir()],
    )


if __name__ == "__main__":
    main()
