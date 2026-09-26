const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const response = (payload, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
});

test('学期管理支持编辑、归档和恢复', async () => {
  const html = loadWorkbenchHtml();
  let terms = [
    { id: 1, code: '2026-S1', name: '2026年第一学期', starts_on: '2026-02-23', ends_on: '2026-07-10', status: 'active' },
    { id: 2, code: '2026-S2', name: '2026年第二学期', starts_on: null, ends_on: null, status: 'active' },
    { id: 3, code: '2025-S2', name: '2025年第二学期', starts_on: null, ends_on: null, status: 'archived' },
  ];
  let activeTermId = 1;
  const emptyState = { schema: 4, teacher: { name: '', subject: '初中英语' }, classes: [], students: [], exams: [], todos: [] };
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'http://127.0.0.1:8765/workbench?token=test-token',
    beforeParse(window) {
      window.fetch = async (url, options = {}) => {
        const target = String(url);
        const method = options.method || 'GET';
        if (target === '/api/v1/terms?include_archived=true') return response(terms);
        if (target === '/api/v1/terms' && method === 'GET') return response(terms.filter(term => term.status === 'active'));
        if (target === '/api/v1/terms/current' && method === 'GET') return response(terms.find(term => term.id === activeTermId));
        if (target === '/api/v1/terms/current' && method === 'PUT') {
          activeTermId = Number(JSON.parse(options.body).term_id);
          return response(terms.find(term => term.id === activeTermId));
        }
        const cacheMatch = target.match(/^\/api\/v1\/terms\/(\d+)\/cache$/);
        if (cacheMatch && method === 'GET') {
          return response({ term_id: Number(cacheMatch[1]), sessions: 2, messages: 4, exam_paper_memories: 1, analysis_runs: 2, analysis_evidence: 3, analysis_events: 0, llm_usage_records: 2, student_profile_drafts: 1, evaluation_drafts: 0, error_cause_candidates: 0, message_attachments: 0, active_runs: 0 });
        }
        const clearCacheMatch = target.match(/^\/api\/v1\/terms\/(\d+)\/cache\/clear$/);
        if (clearCacheMatch && method === 'POST') {
          return response({ ok: true, removed: { sessions: 2, messages: 4, exam_paper_memories: 1 }, temporary_chat_files_removed: 0 });
        }
        const patchMatch = target.match(/^\/api\/v1\/terms\/(\d+)$/);
        if (patchMatch && method === 'PATCH') {
          const id = Number(patchMatch[1]);
          terms = terms.map(term => term.id === id ? { ...term, ...JSON.parse(options.body) } : term);
          return response(terms.find(term => term.id === id));
        }
        const archiveMatch = target.match(/^\/api\/v1\/terms\/(\d+)\/archive$/);
        if (archiveMatch && method === 'POST') {
          const id = Number(archiveMatch[1]);
          terms = terms.map(term => term.id === id ? { ...term, status: 'archived' } : term);
          return response(terms.find(term => term.id === id));
        }
        const restoreMatch = target.match(/^\/api\/v1\/terms\/(\d+)\/restore$/);
        if (restoreMatch && method === 'POST') {
          const id = Number(restoreMatch[1]);
          terms = terms.map(term => term.id === id ? { ...term, status: 'active' } : term);
          return response(terms.find(term => term.id === id));
        }
        if (target === '/api/v1/runtime') return response({ version: '0.7.0', schema: '0006' });
        if (/^\/api\/v1\/terms\/\d+\/workspace-state$/.test(target)) return response({ state: emptyState, revision: 1, updated_at: null });
        return response({ detail: `unexpected ${method} ${target}` }, 404);
      };
    },
  });

  try {
    const doc = dom.window.document;
    await wait(500);
    [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === 'settings').click();
    doc.querySelector('[data-act="term-manage"]').click();
    await wait(80);
    assert.match(doc.getElementById('modalBody').textContent, /2025年第二学期/);
    assert.match(doc.getElementById('modalBody').textContent, /已删除（可恢复）/);
    doc.querySelector('[data-act="term-manage-cache"][data-id="3"]').click();
    await wait(80);
    assert.match(doc.getElementById('modalBody').textContent, /试卷 AI 记忆：1/);
    doc.querySelector('[data-act="term-manage-cache-confirm"][data-id="3"]').click();
    await wait(100);

    doc.querySelector('[data-act="term-manage-edit"][data-id="1"]').click();
    doc.getElementById('m-term-edit-name').value = '2026年春季学期';
    doc.getElementById('m-term-edit-save').click();
    await wait(100);
    assert.equal(doc.getElementById('termSelect').options[0].textContent, '2026年春季学期');

    doc.querySelector('[data-act="term-manage-archive"][data-id="2"]').click();
    doc.querySelector('[data-act="term-manage-archive-confirm"][data-id="2"]').click();
    await wait(100);
    assert.equal(doc.getElementById('termSelect').options.length, 1);

    doc.querySelector('[data-act="term-manage-restore"][data-id="3"]').click();
    await wait(100);
    assert.equal(doc.getElementById('termSelect').options.length, 2);
  } finally {
    dom.window.close();
  }
});
