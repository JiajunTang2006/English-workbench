#!/usr/bin/env python3
"""在 PyInstaller 打包后重建 teachmate-runtime 内被丢弃的 pnpm symlink。

根因：PyInstaller `--add-data src:dst` 只收实体文件；pnpm 顶层 node_modules
里指向 .pnpm 实体的相对 symlink 被视为悬空链接而丢弃（.pnpm 实体完整保留）。
缺少这些 symlink，Node 模块解析失败，harness CLI 启动报 ERR_MODULE_NOT_FOUND。

用法: python3 tools/fix_runtime_symlinks.py <app内teachmate-runtime目录> <源码teachmate-runtime目录>
"""
from __future__ import annotations

import os
import sys


def iter_symlinks(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                yield path
        for name in dirnames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                yield path


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: fix_runtime_symlinks.py <bundled_rt> <source_rt>", file=sys.stderr)
        return 2
    bundled_rt = os.path.abspath(sys.argv[1])
    source_rt = os.path.abspath(sys.argv[2])
    if not (os.path.isdir(bundled_rt) and os.path.isdir(source_rt)):
        print(f"✗ 目录不存在: bundled={bundled_rt} source={source_rt}", file=sys.stderr)
        return 1

    created = 0
    skipped_existing = 0
    missing_target = 0

    for src_link in list(iter_symlinks(source_rt)):
        target = os.readlink(src_link)
        rel = os.path.relpath(src_link, source_rt)
        dest = os.path.join(bundled_rt, rel)

        # 以 dest 所在目录为基准解析相对 target，并确保目标确实位于
        # bundle 内。不能因为构建机上仍有源码目录就接受外部链接。
        resolved = os.path.normpath(os.path.join(os.path.dirname(dest), target))
        try:
            inside_bundle = os.path.commonpath([bundled_rt, resolved]) == bundled_rt
        except ValueError:
            inside_bundle = False
        if inside_bundle and (os.path.exists(resolved) or os.path.islink(resolved)):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.islink(dest) and os.readlink(dest) == target:
                skipped_existing += 1
                continue
            if os.path.lexists(dest):
                os.remove(dest)
            os.symlink(target, dest)
            created += 1
        else:
            missing_target += 1

    restored = sum(1 for _ in iter_symlinks(bundled_rt))
    expected = sum(1 for _ in iter_symlinks(source_rt))
    print(
        f"symlink 重建: 新建 {created} / 已存在 {skipped_existing} / "
        f"目标缺失 {missing_target}；bundle 内共 {restored}，源码共 {expected}"
    )

    boot = os.path.join(
        bundled_rt, "harness", "node_modules", "@deepseek-ai", "dsh-app-boot"
    )
    if os.path.exists(boot):
        print("✓ 关键包 @deepseek-ai/dsh-app-boot 可解析")
        return 0
    print("✗ 关键包 @deepseek-ai/dsh-app-boot 仍缺失", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
