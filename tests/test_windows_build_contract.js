const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

test('Windows 默认构建使用稳定 Python Agent 且不要求 Node', () => {
  const script = read('build_windows.ps1');
  assert.match(script, /\[switch\]\$WithHarness/);
  assert.match(script, /if \(\$WithHarness -and -not \$RuntimeReady\)/);
  assert.match(script, /Building the stable Python Agent edition/);
  assert.match(script, /if \(\$WithHarness\) \{\s*\$PyInstallerArgs \+= @\(/s);
  assert.match(script, /plugins;plugins/);
  assert.match(script, /sys\.version_info\[:2\] == \(3,11\)/);
  assert.match(script, /build package is incomplete or was not fully extracted/);
  assert.match(script, /if \(\$WithHarness\) \{\s*foreach \(\$HarnessPythonProject/s);
  assert.doesNotMatch(script, /Invoke-Checked \$Python @\('-m', 'pip', 'install', '-e'.*\nInvoke-Checked \$Python @\('-m', 'pip', 'install', '-e'/s);
  assert.doesNotMatch(script, /cordis\.headless\.yml/);
});

test('Windows Harness 可选构建明确要求 Node 22.19+', () => {
  const script = read('build_windows.ps1');
  const guide = read('WINDOWS_GUIDE.md');
  assert.match(script, /Node\.js 22\.19\+/);
  assert.match(script, /major === 22 && minor < 19/);
  assert.match(guide, /Node\.js 22\.19/);
  assert.match(guide, /默认稳定版不需要 Node\.js/);
});

test('macOS 和 Windows 构建都会带上内置插件清单', () => {
  const windowsScript = read('build_windows.ps1');
  const macScript = read('build_macos_app.sh');
  assert.match(windowsScript, /plugins;plugins/);
  assert.match(macScript, /plugins:plugins/);
});
