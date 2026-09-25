// ================================================================
// U5-05 UI 自动化：Playwright 关键流程配置
//
// 测试不依赖真实 API Key：Provider 配置缺失时 TeachMate 页面仍
// 能为离线教师提供数据浏览与就绪检查，e2e 覆盖这些"可离线完成"
// 的关键路径；需要 AI 的分析路径通过前端门禁提示验证其存在性。
//
// 运行：npm run test:e2e
//
// webServer (S0-03)：
//   - 由 tools/run_e2e_server.py 自动拉起 FastAPI 测试服务：
//     独立临时数据目录 + 固定测试 Token + 端口 18323；
//   - 健康检查使用 /health，等待服务就绪后再跑测试；
//   - reuseExistingServer=false：绝不复用用户可能正在运行的正式服务，
//     保证每次命令都从干净状态启动，并在结束后关闭服务、释放端口。
// ================================================================
const { defineConfig } = require('@playwright/test');
const fs = require('fs');

const E2E_PORT = 18323;
const E2E_TOKEN = 'workbench-e2e-test-token-18323';

function pythonCommand() {
  const venvPython = process.platform === 'win32'
    ? '.venv\\Scripts\\python.exe'
    : '.venv/bin/python';
  if (fs.existsSync(venvPython)) return venvPython;
  return 'python3';
}

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  retries: 0,
  // 测试失败时保留本地诊断产物，但不上传任何 token。
  // E2E_OUTPUT_DIR 供发布检查把输出隔离到独立临时目录（避免批量清理
  // 触碰项目内 test-results）。
  outputDir: process.env.E2E_OUTPUT_DIR || 'test-results',
  use: {
    baseURL: `http://127.0.0.1:${E2E_PORT}`,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `${pythonCommand()} tools/run_e2e_server.py --port ${E2E_PORT} --token ${E2E_TOKEN}`,
    url: `http://127.0.0.1:${E2E_PORT}/health`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'narrow', use: { viewport: { width: 1100, height: 800 } } },
  ],
});
