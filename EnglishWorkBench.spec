# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['backend.app.main', 'backend.app.factory', 'desktop_shell', 'blank_launcher', 'windows_launcher']
hiddenimports += collect_submodules('deepseek_harness')
hiddenimports += collect_submodules('backend.app')
hiddenimports += collect_submodules('backend.migrations.versions')
hiddenimports += collect_submodules('webview')


a = Analysis(
    ['/Users/tangjiajun/Desktop/workbench/blank_launcher.py'],
    pathex=['/Users/tangjiajun/Desktop/workbench', '/Users/tangjiajun/Desktop/workbench/vendor/deepseek-harness-upstream/python/sdk/src'],
    binaries=[],
    datas=[('/Users/tangjiajun/Desktop/workbench/workbench.html', '.'), ('/Users/tangjiajun/Desktop/workbench/workbench-assets', 'workbench-assets'), ('/Users/tangjiajun/Desktop/workbench/plugins', 'plugins'), ('/Users/tangjiajun/Desktop/workbench/assets/app-icon.png', 'assets'), ('/Users/tangjiajun/Desktop/workbench/backend/alembic.ini', 'backend'), ('/Users/tangjiajun/Desktop/workbench/backend/migrations', 'backend/migrations'), ('/Users/tangjiajun/Desktop/workbench/backend/app/agent/prompts', 'backend/app/agent/prompts'), ('/Users/tangjiajun/Desktop/workbench/backend/app/agent/education_bridge', 'backend/app/agent/education_bridge'), ('/Users/tangjiajun/Desktop/workbench/teachmate-runtime', 'teachmate-runtime')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EnglishWorkBench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/Users/tangjiajun/Desktop/workbench/assets/app-icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EnglishWorkBench',
)
app = BUNDLE(
    coll,
    name='EnglishWorkBench.app',
    icon='/Users/tangjiajun/Desktop/workbench/assets/app-icon.icns',
    bundle_identifier='com.englishworkbench.desktop',
)
