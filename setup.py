# 兼容仅识别 setup.py 的旧版 pip；元数据以 pyproject.toml 为准，此处与之保持一致。
from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).resolve().parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

setup(
    name="qqmusic-crawler",
    version="0.1.0",
    description="QQ Music artist and song crawler (educational purpose).",
    long_description=README,
    long_description_content_type="text/markdown",
    python_requires=">=3.8",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "httpx>=0.27.0",
        "fastapi>=0.115.0",
        "jinja2>=3.1.4",
        "loguru>=0.7.2",
        "pydantic>=2.8.2",
        "pydantic-settings>=2.3.4",
        "python-dotenv>=1.0.1",
        "sqlalchemy>=2.0.31",
        "tenacity>=8.5.0",
        "uvicorn>=0.30.0",
    ],
)
