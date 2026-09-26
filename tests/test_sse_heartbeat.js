const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'workbench-assets');
const assets = path.join(__dirname, '..', 'backend', 'app', 'routers');
const read = p => fs.readFileSync(p, 'utf8');

test('前端 SSE 客户端：收到任意字节（含注释心跳帧）即刷新连接心跳', () => {
  const events = read(path.join(root, 'teachmate-events.js'));
  // 读取循环必须在拿到 chunk 后立即刷新心跳，而不是只在解出 data: 事件时
  //（后端长任务会发 ": keep-alive" 注释帧，注释帧不会进 _processFrame 的
  // data 分支，若在此处才刷新则 15s 无事件仍被误判超时）。
  assert.match(events, /await reader\.read\(\)/);
  assert.match(events, /if \(chunk\.done\) break;/);
  assert.ok(
    /chunk\.done\) break;[\s\S]*?_startHeartbeat\(\)/.test(events),
    '读取循环中必须刷新心跳（chunk.done 之后立即调用 _startHeartbeat）'
  );
  // 前端心跳超时仍保留 15s 设计值，说明依赖后端保活帧
  assert.match(events, /HEARTBEAT_TIMEOUT_MS = 15000/);
});

test('SSE 心跳注释帧文本与前端协议一致', () => {
  const events = read(path.join(root, 'teachmate-events.js'));
  // 前端按 \n\n 分帧；注释帧以 ":" 开头是 SSE 规范，_processFrame 会忽略
  assert.match(events, /buffer\.indexOf\('\\n\\n'\)/);
});

test('后端 SSE 心跳间隔常量存在且小于前端超时（15s）', () => {
  const router = read(path.join(assets, 'agent.py'));
  assert.match(router, /SSE_HEARTBEAT_INTERVAL_SECONDS = 10\.0/);
  assert.match(router, /HEARTBEAT_TIMEOUT_MS=15s/);
});

test('后端 event_stream 在无事件时按间隔产出心跳帧', () => {
  const router = read(path.join(assets, 'agent.py'));
  // 心跳注释帧使用 SSE 注释行语法（冒号开头的行），非 data: 事件
  assert.match(router, /yield ": keep-alive\\n\\n"/);
  assert.match(router, /last_yield/);
  // 心跳间隔比较必须在同一轮询循环内、事件产出前检查
  assert.match(router, /now - last_yield >= SSE_HEARTBEAT_INTERVAL_SECONDS/);
});