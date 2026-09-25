'use strict';

const path = require('path');
const { spawnSync } = require('child_process');

// Playwright 会为子进程启用彩色输出。宿主同时注入 NO_COLOR 时，Node 会
// 对每个 worker 打印冲突警告；只在测试子进程中移除该变量，保留其余告警。
const environment = { ...process.env };
delete environment.NO_COLOR;

const cli = path.join(__dirname, '..', 'node_modules', '@playwright', 'test', 'cli.js');
const result = spawnSync(process.execPath, [cli, 'test', ...process.argv.slice(2)], {
  cwd: path.join(__dirname, '..'),
  env: environment,
  stdio: 'inherit',
});

if (result.error) throw result.error;
process.exit(result.status ?? 1);
