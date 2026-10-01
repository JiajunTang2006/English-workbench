const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const views = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-views.js'), 'utf8');
const interactions = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8');
const router = fs.readFileSync(path.join(root, 'backend', 'app', 'routers', 'agent.py'), 'utf8');

test('未配置模型时前端不回退到列表第一项', () => {
  assert.match(views, /currentModelName[^\n]*未配置模型/);
  assert.match(interactions, /modelName[^\n]*未配置模型/);
  assert.match(interactions, /is-unconfigured[^\n]*未配置/);
  assert.doesNotMatch(views, /find\([^\n]+\)\s*\|\|\s*savedModels\[0\]/);
  assert.doesNotMatch(interactions, /find\([^\n]+\)\s*\|\|\s*savedModels\[0\]/);
});

test('未配置模型时快捷卡保留编辑入口，设置由教师主动打开', () => {
  assert.match(views, /data-provider-unavailable/);
  assert.match(views, /设置 → 模型/);
  assert.doesNotMatch(interactions, /providerUnavailable.*tmOpenAgentSettings\('models'\)/);
  assert.match(views, /var disabled = composerLocked/);
  assert.match(views, /模型暂不可用，可以先编辑问题/);
  assert.match(interactions, /if \(modelUnavailable\) \{ showToast\(modelUnavailable\); return; \}/);
});

test('TeachMate 文件夹创建使用应用内表单', () => {
  assert.match(interactions, /tmFolderNameInput/);
  assert.match(interactions, /tm-folder-create-confirm/);
  assert.doesNotMatch(interactions, /function tmCreateFolder\(\)[\s\S]{0,240}window\.prompt/);
});

test('无模型档案时后端不伪造 legacy-current 模型档案', () => {
  assert.match(router, /if not stored_profiles and cfg\.text_model_profile_id and cfg\.text_api_key_configured:/);
});
