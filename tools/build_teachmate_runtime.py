#!/usr/bin/env python3
"""Build a portable Harness runtime for PyInstaller distributions.

The upstream workspace uses pnpm workspace links during development.  This
script turns those links into a relocatable ``teachmate-runtime/harness`` tree
and copies a local Node executable beside it, so the desktop app does not need
the user's source checkout or a globally installed Node.js.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor" / "deepseek-harness-upstream"


def run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def ignore_source(path: str, names: list[str]) -> set[str]:
    ignored = {
        "node_modules", ".git", ".turbo", ".cache",
        # 运行时只需要生产代码，测试夹具/快照可能包含示例会话或
        # 模拟用户数据，不应进入最终桌面安装包。
        "test", "tests", "__tests__", "fixtures", "snapshots", "test-support",
    }
    ignored_suffixes = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".snap")
    return {
        name for name in names
        if name in ignored or name.endswith(ignored_suffixes)
    }


def copy_source_tree(destination: Path) -> None:
    for name in ("apps", "packages", "vendor"):
        source = UPSTREAM / name
        if source.is_dir():
            shutil.copytree(
                source,
                destination / name,
                symlinks=os.name != "nt",
                ignore=ignore_source,
            )
    examples = UPSTREAM / "examples" / "teachmate"
    # Copy the entire teachmate examples directory, which includes:
    #   cordis.yml       — Web profile overlay
    #   cordis.sdk.yml   — JSON-RPC SDK composition (production-safe, headless)
    #   README.md        — documentation
    shutil.copytree(
        examples,
        destination / "examples" / "teachmate",
        symlinks=os.name != "nt",
        ignore=ignore_source,
    )


def create_directory_link(link_path: Path, target: Path) -> None:
    """Create a relocatable link without requiring Windows admin rights."""

    if os.name == "nt":
        if not target.is_dir():
            shutil.copy2(target, link_path)
            return
        # Directory junctions do not require Developer Mode or elevation.
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target.resolve())],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return
    relative = os.path.relpath(target, link_path.parent)
    link_path.symlink_to(relative)


def rewrite_workspace_links(tree: Path, source_root: Path) -> int:
    """Make deploy's absolute workspace symlinks point inside ``tree``.

    Materialise the traversal before changing links.  Mutating a directory
    while ``Path.rglob`` is lazily scanning it can otherwise skip sibling
    links, leaving a runtime that works only on the build machine.
    """
    rewritten = 0
    source_root = source_root.resolve()
    source_root_text = source_root.as_posix()
    for link in [path for path in tree.rglob("*") if path.is_symlink()]:
        if not link.is_symlink():
            continue
        raw_target = os.readlink(link)
        resolved = (link.parent / raw_target).resolve()
        try:
            relative_source = resolved.relative_to(source_root)
        except ValueError:
            # macOS exposes temporary directories through ``/private``.  A
            # relative pnpm workspace link copied from a deploy staging tree
            # can therefore resolve as ``/private/.../Users/...`` even though
            # its real source is below ``/Users/...``.  Recover the canonical
            # workspace suffix before deciding that the link is external.
            resolved_text = resolved.as_posix()
            marker = resolved_text.find(source_root_text)
            if marker < 0:
                continue
            relative_source = Path(resolved_text[marker:]).relative_to(source_root)
        relocated = tree / relative_source
        link.unlink()
        create_directory_link(link, relocated)
        rewritten += 1
    return rewritten


def validate_relocatable_links(tree: Path) -> None:
    """Reject broken links and links that escape the portable runtime."""
    root = tree.resolve()
    broken: list[str] = []
    external: list[str] = []
    for link in [path for path in tree.rglob("*") if path.is_symlink()]:
        target = (link.parent / os.readlink(link)).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            external.append(f"{link.relative_to(tree)} -> {target}")
            continue
        if not target.exists():
            broken.append(f"{link.relative_to(tree)} -> {target}")
    if external or broken:
        details = [
            *(f"外部链接: {item}" for item in external[:10]),
            *(f"失效链接: {item}" for item in broken[:10]),
        ]
        raise RuntimeError(
            "TeachMate 运行时不是可移植的：" + "; ".join(details)
        )


def _collect_workspace_packages(tree: Path) -> dict[str, Path]:
    """Map every workspace package name to its source directory inside *tree*."""
    packages: dict[str, Path] = {}
    for root_dir in ("apps", "packages", "vendor", "examples"):
        search_root = tree / root_dir
        if not search_root.is_dir():
            continue
        for pkg_json in search_root.rglob("package.json"):
            if "node_modules" in pkg_json.parts:
                continue
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            name = data.get("name")
            if name:
                packages[name] = pkg_json.parent
    return packages


def link_workspace_packages(tree: Path) -> int:
    """Create node_modules symlinks for workspace packages that ``pnpm deploy
    --prod`` omitted (peerDependencies are not included in prod deploy).

    Returns the number of symlinks created.
    """
    workspace = _collect_workspace_packages(tree)
    node_modules = tree / "node_modules"
    created = 0
    for name, source_dir in workspace.items():
        link_path = node_modules / name
        if link_path.exists() or link_path.is_symlink():
            continue
        # Ensure parent (e.g. node_modules/@deepseek-ai) exists
        link_path.parent.mkdir(parents=True, exist_ok=True)
        create_directory_link(link_path, source_dir)
        created += 1
    return created


def _build_pnpm_store_map(tree: Path) -> dict[str, Path]:
    """Scan the ``.pnpm`` virtual store and map every package name to its
    resolved directory.

    Each ``.pnpm/<store-dir>/node_modules/<pkg>`` entry is a candidate.
    Scoped packages live under ``node_modules/@scope/<pkg>``.
    """
    pnpm_store = tree / "node_modules" / ".pnpm"
    if not pnpm_store.is_dir():
        return {}
    store_map: dict[str, Path] = {}
    for store_dir in pnpm_store.iterdir():
        if not store_dir.is_dir() or store_dir.is_symlink():
            continue
        inner_nm = store_dir / "node_modules"
        if not inner_nm.is_dir():
            continue
        for entry in inner_nm.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("@") and not entry.is_symlink():
                for pkg in entry.iterdir():
                    if pkg.is_dir():
                        full_name = f"{entry.name}/{pkg.name}"
                        store_map.setdefault(full_name, pkg)
            elif entry.name not in store_map:
                store_map[entry.name] = entry
    return store_map


def link_store_dependencies(tree: Path) -> int:
    """Link external (non-workspace) dependencies from the ``.pnpm`` store into
    the flat ``node_modules`` so that workspace packages symlinked in by
    :func:`link_workspace_packages` can resolve their own external imports.

    Returns the number of symlinks created.
    """
    store_map = _build_pnpm_store_map(tree)
    if not store_map:
        return 0
    node_modules = tree / "node_modules"

    # Collect every dependency declared by any workspace package in the tree
    needed: set[str] = set()
    for pkg_json in tree.rglob("package.json"):
        if "node_modules" in pkg_json.parts:
            continue
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for section in ("dependencies", "peerDependencies"):
            needed.update(data.get(section, {}).keys())

    created = 0
    for dep_name in needed:
        link_path = node_modules / dep_name
        if link_path.exists() or link_path.is_symlink():
            continue
        source = store_map.get(dep_name)
        if source is None:
            continue
        link_path.parent.mkdir(parents=True, exist_ok=True)
        create_directory_link(link_path, source)
        created += 1
    return created


SDK_RUNTIME_PACKAGES = (
    "dsh-sdk-jsonrpc-demo", "dsh-sdk-jsonrpc-server", "dsh-llm-deepseek",
    "dsh-agent-spine-demo", "dsh-session-persistence-jsonl",
    "dsh-session-checkpoint-policy", "dsh-token-meter",
    "dsh-compaction-basic", "dsh-tools",
)


def include_sdk_external_dependencies(tree: Path) -> int:
    """Complete the production dependency graph for the actual SDK profile.

    CLI deploy does not contain every SDK plugin dependency. Resolve each from
    its installed source owner and keep versions local to that owner, without
    copying development dependencies or links to the build machine.
    """
    workspace = _collect_workspace_packages(UPSTREAM)
    queue = []
    for short_name in SDK_RUNTIME_PACKAGES:
        name = "@deepseek-ai/" + short_name
        source = workspace.get(name)
        if source is None:
            raise RuntimeError(f"SDK workspace package missing: {name}")
        queue.append((source, tree / source.relative_to(UPSTREAM)))
    visited = set()
    copied = 0
    while queue:
        source, destination = queue.pop()
        identity = (source.resolve(), destination)
        if identity in visited:
            continue
        visited.add(identity)
        metadata = json.loads((source / "package.json").read_text(encoding="utf-8"))
        dependencies = dict(metadata.get("dependencies", {}))
        dependencies.update(metadata.get("peerDependencies", {}))
        optional = metadata.get("peerDependenciesMeta", {})
        for name in dependencies:
            if name in workspace:
                package_source = workspace[name]
                package_destination = tree / package_source.relative_to(UPSTREAM)
            else:
                candidates = [parent / "node_modules" / name
                              for parent in (source, *source.parents)
                              if parent == UPSTREAM or UPSTREAM in parent.parents]
                package_source = next((p.resolve() for p in candidates if p.is_dir()), None)
                if package_source is None:
                    if optional.get(name, {}).get("optional"):
                        continue
                    raise RuntimeError(f"SDK production dependency missing: {name} ({source.name})")
                package_metadata = json.loads((package_source / "package.json").read_text(encoding="utf-8"))
                package_destination = (tree / "node_modules" / ".teachmate-sdk"
                                       / name / str(package_metadata["version"]))
                if not package_destination.is_dir():
                    shutil.copytree(package_source, package_destination,
                                    symlinks=False, ignore=ignore_source)
                    copied += 1
            link = destination / "node_modules" / name
            if not link.exists() and not link.is_symlink():
                link.parent.mkdir(parents=True, exist_ok=True)
                create_directory_link(link, package_destination)
            queue.append((package_source, package_destination))
    return copied


def build(output: Path, *, no_build: bool, node_binary: str | None) -> None:
    if not UPSTREAM.is_dir():
        raise SystemExit(f"找不到 Harness 源码：{UPSTREAM}")
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("找不到 pnpm，请先安装 pnpm 后再构建桌面安装包")
    node = node_binary or shutil.which("node")
    if node is None:
        raise SystemExit("找不到 Node.js，请先安装 Node.js 后再构建桌面安装包")

    if not no_build:
        run([pnpm, "run", "build:web"], cwd=UPSTREAM)
        run([pnpm, "run", "build:lib:host"], cwd=UPSTREAM)
        run([pnpm, "run", "build:lib:client"], cwd=UPSTREAM)

    with tempfile.TemporaryDirectory(prefix="teachmate-runtime-") as temporary:
        staging = Path(temporary)
        deploy = staging / "deploy"
        run(
            [pnpm, "--filter", "@deepseek-ai/dsh", "deploy", "--prod", "--legacy", str(deploy)],
            cwd=UPSTREAM,
        )

        runtime = staging / "teachmate-runtime"
        harness = runtime / "harness"
        # On Windows, dereference pnpm links so the later ZIP is portable and
        # does not depend on Developer Mode or preserved reparse points.
        shutil.copytree(deploy, harness, symlinks=os.name != "nt")
        copy_source_tree(harness)
        rewritten = rewrite_workspace_links(harness, UPSTREAM)
        if rewritten:
            print(f"Rewrote {rewritten} workspace links to portable targets")

        # pnpm deploy --prod omits peerDependencies; link all workspace
        # packages into node_modules so runtime imports resolve.
        linked = link_workspace_packages(harness)
        if linked:
            print(f"Linked {linked} missing workspace packages into node_modules")

        # Workspace packages symlinked above need their own external deps
        # (zod, @opentelemetry/*, etc.) which are in the .pnpm store but
        # not in the flat node_modules. Link them here.
        ext_linked = link_store_dependencies(harness)
        if ext_linked:
            print(f"Linked {ext_linked} external dependencies from .pnpm store")

        include_sdk_external_dependencies(harness)
        validate_relocatable_links(harness)

        node_dir = runtime / "node"
        node_dir.mkdir(parents=True)
        node_name = "node.exe" if os.name == "nt" else "node"
        shutil.copy2(node, node_dir / node_name)

        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(runtime, output, symlinks=os.name != "nt")
    file_size = sum(
        path.stat().st_size
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    print(f"TeachMate 运行时已生成：{output}")
    print(f"文件体积：{file_size / 1024 / 1024:.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "teachmate-runtime")
    parser.add_argument("--no-build", action="store_true", help="复用已有 Harness 构建产物")
    parser.add_argument("--node-binary", help="指定要随安装包分发的 Node 可执行文件")
    args = parser.parse_args()
    build(args.output.resolve(), no_build=args.no_build, node_binary=args.node_binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
