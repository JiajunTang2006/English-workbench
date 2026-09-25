#!/usr/bin/env python3
"""统一发布质量门禁 (S0-04)

`npm run release_check` 的入口，按固定顺序执行全部检查：

  01 环境与版本一致性
  02 JavaScript 语法检查  (npm run check)
  03 前端 Node 测试        (npm test)
  04 后端 pytest 测试       (.venv/bin/python -m pytest)
  05 空数据库创建与迁移冒烟
  06 浏览器端到端测试       (npm run test:e2e)
  07 Python 依赖完整性检查
  08 敏感文件检查
  09 生成发布校验清单

规则：
- 任一阶段失败立即以非零状态退出；
- 只有全部阶段通过才生成 release-manifest.sha256（先写临时文件再原子替换，
  失败时保留上一次有效清单）；
- 所有测试使用独立临时数据目录（e2e 由 playwright webServer 自管临时目录）；
- 成功后清理临时目录；失败时打印诊断路径（不含密钥）；
- 不构建正式安装包。

用法::

    npm run release_check      # 或 python3 tools/release_check.py
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.version import APP_VERSION, SCHEMA_REVISION

# 校验清单排除集合（与 .gitignore 对齐；release 交付说明与 vendor 源码在库内，需进清单）
MANIFEST_EXCLUDED_PARTS = {
    ".venv", ".git", "node_modules", "build", "dist", "__pycache__",
    ".pytest_cache", ".workbuddy", ".sessions", "test-results",
    "playwright-report", "teachmate-runtime", ".pnpm-store", "test_data",
}

# 敏感文件扩展名/文件名模式
SENSITIVE_SUFFIXES = (
    ".env", ".key", ".pem", ".p12", ".pfx", ".jks",
    ".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite3",
)
SENSITIVE_NAME_PATTERNS = (".env", ".env.")
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{24,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bghp_[0-9A-Za-z]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?(PRIVATE|PUBLIC) KEY-----"),
)
ALLOWED_PHONE_PLACEHOLDERS = {"13800138000", "13900139000", "13800000000", "13912345678"}
# 手机号匹配要求后随非字母数字，避免 SHA-256 哈希中的连续数字误报
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?![\da-zA-Z])")

failures: list[str] = []


def stage_started(name: str) -> float:
    print(f"\n===== [{name}] 开始 =====", flush=True)
    return time.monotonic()


def stage_finished(name: str, start: float) -> None:
    print(f"===== [{name}] 通过 （{time.monotonic() - start:.1f}s） =====\n", flush=True)


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(str(part) for part in command), flush=True)
    environment = os.environ.copy()
    if env:
        environment.update(env)
    result = subprocess.run(command, cwd=ROOT, env=environment)
    if result.returncode != 0:
        fail(f"命令失败（退出码 {result.returncode}）: {' '.join(command)}")
        raise SystemExit(1)


def fail(message: str) -> None:
    failures.append(message)
    print(f"\n[FAIL] {message}", flush=True)


def backend_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def backend_python() -> Path:
    python = ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        fail("未找到 .venv/bin/python，请先安装锁定的后端依赖")
        raise SystemExit(1)
    return python


# --- 01 环境与版本一致性 ---

def check_env_and_version() -> None:
    start = stage_started("01 环境与版本一致性")
    version_py = (ROOT / "backend" / "app" / "version.py").read_text(encoding="utf-8")
    app_version_match = re.search(r'APP_VERSION = "([^"]+)"', version_py)
    schema_match = re.search(r'SCHEMA_REVISION = "([^"]+)"', version_py)
    if not app_version_match or not schema_match:
        fail("backend/app/version.py 缺少 APP_VERSION 或 SCHEMA_REVISION")
        raise SystemExit(1)
    app_version, schema_revision = app_version_match.group(1), schema_match.group(1)
    if app_version != APP_VERSION:
        fail(f"应用版本不一致：version.py={app_version}，期望 {APP_VERSION}")
    if schema_revision != SCHEMA_REVISION:
        fail(f"迁移版本不一致：version.py={schema_revision}，期望 {SCHEMA_REVISION}")

    package_json = (ROOT / "package.json").read_text(encoding="utf-8")
    npm_version_match = re.search(r'"version":\s*"([^"]+)"', package_json)
    npm_version = npm_version_match.group(1) if npm_version_match else ""
    if npm_version != APP_VERSION:
        fail(f"package.json 版本不一致：{npm_version}，期望 {APP_VERSION}")

    result = subprocess.run(
        [str(backend_python()), "-m", "alembic", "-c", "backend/alembic.ini", "heads"],
        cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(f"alembic heads 执行失败：{result.stderr.strip()[-200:]}")
    else:
        heads_text = " ".join(result.stdout.split())
        if SCHEMA_REVISION not in heads_text:
            fail(f"alembic head 与 SCHEMA_REVISION 不一致：heads='{heads_text}' 期望 {SCHEMA_REVISION}")
        print(f"    alembic head = {heads_text}")
    print(f"    应用版本 = {app_version}   迁移 = {schema_revision}   npm 版本 = {npm_version}")
    if failures:
        raise SystemExit(1)
    stage_finished("01 环境与版本一致性", start)


# --- 02/03/04/06 命令型阶段 ---

def run_command_stage(name: str, command: list[str], *, env: dict[str, str] | None = None) -> None:
    start = stage_started(name)
    run(command, env=env)
    stage_finished(name, start)


# --- 05 空数据库迁移冒烟 ---

def smoke_migration() -> None:
    start = stage_started("05 空数据库创建与迁移冒烟")
    temp_dir = Path(tempfile.mkdtemp(prefix="release-check-smoke-"))
    print(f"    临时数据目录: {temp_dir}")
    code = (
        "import os, sys;"
        "from pathlib import Path;"
        "from backend.app.config import Settings;"
        "from backend.app.factory import create_app;"
        "import sqlalchemy as sa;"
        "d = Path(os.environ['WB_SMOKE_DATA']);"
        "app = create_app(Settings(data_dir=d));"
        "eng = sa.create_engine(Settings(data_dir=d).database_url);"
        "conn = eng.connect();"
        "ver = conn.execute(sa.text('select version_num from alembic_version')).scalar();"
        "conn.close();"
        f"assert ver == '{SCHEMA_REVISION}', ver;"
        "print('alembic_version =', ver)"
    )
    result = subprocess.run(
        [str(backend_python()), "-c", code],
        cwd=ROOT,
        env={**backend_env(), "WB_SMOKE_DATA": str(temp_dir)},
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-1200:])
        sys.stderr.write(result.stderr[-1200:])
        fail(f"空数据库迁移冒烟失败：{result.stderr.strip()[-200:]}（临时数据目录已保留用于诊断: {temp_dir}）")
        raise SystemExit(1)
    else:
        last_line = result.stdout.strip().splitlines()[-1]
        print("    " + last_line)
    shutil.rmtree(temp_dir, ignore_errors=True)
    print("    临时数据目录已清理")
    stage_finished("05 空数据库创建与迁移冒烟", start)


def run_e2e() -> None:
    """06 浏览器端到端测试。

    输出目录重定向到独立临时目录（E2E_OUTPUT_DIR），避免 Playwright
    清理项目内 test-results 时触发宿主环境的批量删除保护，也不污染仓库。
    """
    start = stage_started("06 浏览器端到端测试")
    output_dir = Path(tempfile.mkdtemp(prefix="release-check-e2e-"))
    print(f"    e2e 输出目录: {output_dir}")
    run(["npm", "run", "test:e2e"], env={"E2E_OUTPUT_DIR": str(output_dir)})
    shutil.rmtree(output_dir, ignore_errors=True)
    print("    e2e 输出目录已清理")
    stage_finished("06 浏览器端到端测试", start)


# --- 07 Python 依赖完整性 ---

def check_dependencies() -> None:
    start = stage_started("07 Python 依赖完整性检查")
    py = backend_python()
    run([str(py), "-m", "pip", "check"])
    import_code = (
        "import importlib, sys\n"
        "mods = ['backend.app.factory', 'deepseek_harness', 'jsonschema', 'alembic', "
        "'pdfplumber', 'PIL', 'openpyxl', 'pytest_asyncio']\n"
        "missing = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as e:\n"
        "        missing.append((m, str(e)[:120]))\n"
        "if missing:\n"
        "    sys.exit('missing imports: ' + '; '.join(f'{m} ({e})' for m, e in missing))\n"
        "print('required imports OK: ' + ', '.join(mods))\n"
    )
    run([str(py), "-c", import_code], env=backend_env())
    stage_finished("07 Python 依赖完整性检查", start)


# --- 08 敏感文件检查 ---

def _tracked_paths() -> list[Path]:
    """读取 Git 跟踪及未忽略文件；无 Git 时回退到排除式目录扫描。"""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout:
        # `git ls-files` 也会返回工作区中已删除但仍处于索引里的路径。
        # 发布检查只应扫描当前实际存在的文件，否则后续 stat/read_bytes
        # 会在“敏感文件检查”阶段因过期清单条目直接崩溃。
        return [
            path for raw_path in result.stdout.split("\0")
            if raw_path
            for path in [ROOT / raw_path]
            if path.exists()
        ]
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in MANIFEST_EXCLUDED_PARTS for part in rel.parts):
            continue
        files.append(path)
    return files


def check_sensitive_files() -> None:
    start = stage_started("08 敏感文件检查")
    violations: list[str] = []
    for path in _tracked_paths():
        rel = path.relative_to(ROOT)
        name = path.name.lower()
        if name.endswith(SENSITIVE_SUFFIXES) or name.startswith(SENSITIVE_NAME_PATTERNS):
            violations.append(f"敏感扩展名/文件名: {rel}")
            continue
        if path.stat().st_size > 1024 * 1024 or ".min." in name:
            continue
        try:
            if b"\x00" in path.read_bytes()[:4096]:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                violations.append(f"疑似密钥: {rel}（{pattern.pattern[:20]}…）")
                break
        for match in PHONE_PATTERN.finditer(text):
            if match.group(0) not in ALLOWED_PHONE_PLACEHOLDERS:
                violations.append(f"疑似手机号: {rel}")
                break
    if violations:
        for item in violations:
            print(f"    [违规] {item}", flush=True)
        fail("敏感文件检查发现违规项（仅列路径与类型，未打印完整内容）")
        raise SystemExit(1)
    print("    未发现密钥、敏感扩展名、数据库文件或疑似真实手机号")
    stage_finished("08 敏感文件检查", start)


# --- 09 生成发布校验清单 ---

def generate_manifest() -> None:
    start = stage_started("09 生成发布校验清单")
    manifest = ROOT / "release-manifest.sha256"
    temporary = manifest.with_suffix(".sha256.tmp")
    # 清单不能包含自身，否则写入完成后其摘要必然立即失效。
    files = [path for path in _tracked_paths() if path not in {manifest, temporary}]
    if not files:
        fail("校验清单文件列表为空")
        raise SystemExit(1)
    with temporary.open("w", encoding="utf-8") as output:
        for path in sorted(files, key=lambda p: str(p)):
            if path.is_symlink():
                # 符号链接条目按其链接目标字符串计算摘要
                digest = hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            output.write(f"{digest}  {path.relative_to(ROOT)}\n")
    temporary.replace(manifest)
    print(f"    已生成：{manifest}（{len(files)} 个文件）")
    stage_finished("09 生成发布校验清单", start)


def main() -> int:
    print(f"English Workbench 发布检查  应用 {APP_VERSION} / 迁移 {SCHEMA_REVISION}")
    print(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    check_env_and_version()
    run_command_stage("02 JavaScript 语法检查", ["node", "tools/check_syntax.js"])
    run_command_stage("03 前端 Node 测试", ["npm", "test"])
    run_command_stage("04 后端测试", [str(backend_python()), "-m", "pytest", "backend/tests", "-q"], env=backend_env())
    smoke_migration()
    run_e2e()
    check_dependencies()
    check_sensitive_files()

    if failures:
        print("\n[发布检查失败] 未生成新的校验清单，上次有效清单保留：")
        for item in failures:
            print(f"  - {item}")
        return 1

    generate_manifest()
    print("\n[发布检查全部通过] 新的校验清单已生成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
