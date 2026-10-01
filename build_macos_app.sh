#!/bin/sh
set -eu

# ── English Workbench — macOS .app build script ──────────────────────
# Improvements:
#   • A standalone formal app that does not overwrite the preserved Blank app
#   • VERSION stamp baked into the app bundle
#   • Pre-build clean of old dist artifacts
#   • Build summary with file size + timestamp
#   • Codesign-admissible structure (no weird symlinks left behind)
# ─────────────────────────────────────────────────────────────────────

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

# Some machines have a full Xcode selected but have not accepted its licence.
# The installed Command Line Tools provide all build/signing commands used here.
if [ -z "${DEVELOPER_DIR:-}" ] && ! /usr/bin/xcrun --find lipo >/dev/null 2>&1 \
  && [ -x /Library/Developer/CommandLineTools/usr/bin/lipo ]; then
  DEVELOPER_DIR=/Library/Developer/CommandLineTools
  export DEVELOPER_DIR
fi

# PyInstaller defaults to a user-level cache on macOS. That cache may contain
# protected files from an older build, so keep build metadata local and
# reproducible for this project instead of failing during --clean.
PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT/.pyinstaller-config}"
export PYINSTALLER_CONFIG_DIR
mkdir -p "$PYINSTALLER_CONFIG_DIR"

# ── Python interpreter ───────────────────────────────────────────────
if [ -n "${PYTHON:-}" ]; then
  PYTHON="$PYTHON"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="python3"
fi

NAME="EnglishWorkBench"
# 以应用版本文件为唯一事实源，避免 Finder 中的包版本落后于实际代码。
VERSION="$($PYTHON -c 'from backend.app.version import APP_VERSION; print(APP_VERSION)')"
BUNDLE_ID="com.englishworkbench.desktop"
BUILD_TIME="$(date '+%Y-%m-%d %H:%M:%S')"

echo "╔══════════════════════════════════════════════════╗"
echo "║  English Workbench — macOS Build                 ║"
echo "║  Version : $VERSION                              ║"
echo "║  Time    : $BUILD_TIME                           ║"
echo "║  Python  : $($PYTHON --version 2>&1)             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Desktop window dependency ───────────────────────────────────────
# pywebview is intentionally optional for local/browser development, but a
# packaged .app needs it to render WorkBench in its own native window.
if ! "$PYTHON" -c "import webview" >/dev/null 2>&1; then
  echo "✗ pywebview is not installed in $PYTHON."
  echo "  Install it with: $PYTHON -m pip install 'pywebview>=5.3,<7'"
  exit 1
fi

# ── Pre-build clean ──────────────────────────────────────────────────
echo "→ Cleaning old build artifacts..."
rm -rf "dist/${NAME}.app" "build/${NAME}"
mkdir -p release

# ── PyInstaller ──────────────────────────────────────────────────────
echo "→ Running PyInstaller..."
RUNTIME_DATA_ARGS=""
if [ -d "$ROOT/teachmate-runtime" ]; then
  RUNTIME_DATA_ARGS="--add-data $ROOT/teachmate-runtime:teachmate-runtime"
  echo "→ Including bundled TeachMate runtime (Node + Harness)..."
else
  echo "⚠ No teachmate-runtime found; packaged app will use the legacy WorkBench fallback."
  echo "  Run: $PYTHON tools/build_teachmate_runtime.py"
fi
"$PYTHON" -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name "$NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon "$ROOT/assets/app-icon.icns" \
  --paths "$ROOT" \
  --add-data "$ROOT/workbench.html:." \
  --add-data "$ROOT/workbench-assets:workbench-assets" \
  --add-data "$ROOT/plugins:plugins" \
  --add-data "$ROOT/assets/app-icon.png:assets" \
  --add-data "$ROOT/backend/alembic.ini:backend" \
  --add-data "$ROOT/backend/migrations:backend/migrations" \
  --add-data "$ROOT/backend/app/agent/prompts:backend/app/agent/prompts" \
  --add-data "$ROOT/backend/app/agent/education_bridge:backend/app/agent/education_bridge" \
  --paths "$ROOT/vendor/deepseek-harness-upstream/python/sdk/src" \
  --collect-submodules deepseek_harness \
  --collect-submodules backend.app \
  --collect-submodules backend.migrations.versions \
  --collect-submodules webview \
  --hidden-import backend.app.main \
  --hidden-import backend.app.factory \
  --hidden-import desktop_shell \
  --hidden-import blank_launcher \
  --hidden-import windows_launcher \
  $RUNTIME_DATA_ARGS \
  "$ROOT/blank_launcher.py"

# ── Verify the .app exists ──────────────────────────────────────────
APP_PATH="dist/${NAME}.app"
if [ ! -d "$APP_PATH" ]; then
  echo "✗ Build failed: $APP_PATH not found."
  exit 1
fi

# Keep the visible version meaningful in Finder and System Information.
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_PATH/Contents/Info.plist" || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP_PATH/Contents/Info.plist" || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$APP_PATH/Contents/Info.plist"

# Ad-hoc signing makes the locally built bundle internally consistent. It does
# not claim Apple Developer notarization.
# ── Restore pnpm symlinks dropped by PyInstaller ─────────────────────
# PyInstaller --add-data 只收实体文件，会把 teachmate-runtime 里 pnpm 顶层
# node_modules 指向 .pnpm 实体的相对 symlink 当悬空链接丢弃，导致 harness CLI
# 启动报 ERR_MODULE_NOT_FOUND。必须在 codesign 之前重建（改内容会使签名失效）。
if [ -d "$ROOT/teachmate-runtime" ]; then
  echo "→ Restoring pnpm symlinks in bundled teachmate-runtime..."
  /usr/bin/python3 "$ROOT/tools/fix_runtime_symlinks.py" \
    "$APP_PATH/Contents/Resources/teachmate-runtime" \
    "$ROOT/teachmate-runtime"
fi

codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

# Deliver both a directly runnable app and a transport-safe zip archive.
RELEASE_APP="release/${NAME}.app"
rm -rf "$RELEASE_APP"
ditto "$APP_PATH" "$RELEASE_APP"

# ── Package as zip ───────────────────────────────────────────────────
echo "→ Creating zip archive..."
ZIP_PATH="release/${NAME}.app.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

# ── Package as DMG (macOS distribution) ──────────────────────────────
# 拖拽式安装镜像：内含 app 本体与指向 /Applications 的软链接。
echo "→ Creating DMG installer..."
DMG_PATH="release/${NAME}_macOS_arm64_Blank_${VERSION}.dmg"
DMG_STAGE="$ROOT/build/dmg-stage"
rm -rf "$DMG_STAGE" 2>/dev/null || true
mkdir -p "$DMG_STAGE"
ditto "$APP_PATH" "$DMG_STAGE/${NAME}.app"
ln -sfn /Applications "$DMG_STAGE/Applications"
rm -f "$DMG_PATH"
hdiutil create -volname "English Workbench" \
  -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"
rm -rf "$DMG_STAGE" 2>/dev/null || true

# ── Build summary ────────────────────────────────────────────────────
ZIP_SIZE=$(du -sh "$ZIP_PATH" | cut -f1)
APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
echo ""
echo "════════════════════════════════════════════════════"
echo "  ✅ Build complete!"
echo "  App : $RELEASE_APP ($APP_SIZE)"
echo "  Zip : $ZIP_PATH ($ZIP_SIZE)"
echo "  Dmg : $DMG_PATH ($DMG_SIZE)"
echo "  Date: $BUILD_TIME"
echo "════════════════════════════════════════════════════"
