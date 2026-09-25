// Education Bridge — DeepSeek Harness Cordis 插件 (B3-03)
//
// 设计：
//   1. 只注册白名单教学工具；
//   2. scope 一律来自服务器注入的 per-session scope 文件
//      （DSH_EDUCATION_SCOPE_ROOT / <sessionId>.json）；模型参数里出现任何 scope 键
//      都会在 bridge_cli 内被拒绝（fail-closed）；
//   3. 工具执行 = child_process.execFile('<python>', ['-m', bridgeModule, ...])，
//      只和本地 bridge_cli 通信；本文件不产生 shell、不读写任意文件、不发起网络请求；
//   4. 任何异常/超时 -> 抛明确错误，绝不静默返回空数据。
//
// 只依赖 Node 内置模块。

import { execFile } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'

export const TOOL_DEFS = [
  {
    name: 'get_exam_analysis_bundle',
    title: '成绩分析数据包',
    description:
      '成绩分析首选工具：一次返回当前考试的核心统计，或当前会话正式附件的预算内摘要。' +
      '应先调用它，通常无需再逐项、逐页读取。',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'get_exam_overview',
    title: '考试概览',
    description:
      '获取当前作用域考试的总体统计（人数、平均分、最高/最低分、合格率、优秀率），数据只读。',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'get_score_distribution',
    title: '分数分布',
    description: '获取当前考试分数段分布（可指定 bin_size，默认 10）。',
    parameters: {
      type: 'object',
      properties: { bin_size: { type: 'integer', description: '分数段间隔，默认 10' } },
      required: [],
    },
  },
  {
    name: 'get_question_list',
    title: '题目统计',
    description: '获取当前考试题目列表（可按题型过滤 question_type）。',
    parameters: {
      type: 'object',
      properties: { question_type: { type: 'string', description: '题型（可选）' } },
      required: [],
    },
  },
  {
    name: 'get_student_trend',
    title: '学生趋势',
    description:
      '获取当前作用域近 N 场（horizon，默认 3，最多 10）考试的平均分趋势，只含匿名编号。',
    parameters: {
      type: 'object',
      properties: { horizon: { type: 'integer', description: '回看考试场次，默认 3' } },
      required: [],
    },
  },
  {
    name: 'get_risk_signals',
    title: '风险信号',
    description: '获取低于风险分数线的学生列表（只返回匿名编号与分数差距）。',
    parameters: {
      type: 'object',
      properties: { threshold: { type: 'number', description: '风险分数线，默认 60' } },
      required: [],
    },
  },
  {
    name: 'get_wrong_questions',
    title: '错题统计',
    description: '获取当前考试知识点覆盖情况（逐题得分未录入时不编造错题率）。',
    parameters: {
      type: 'object',
      properties: { top_n: { type: 'integer', description: '预留，暂不生效' } },
      required: [],
    },
  },
  {
    name: 'get_student_scores',
    title: '学生逐题成绩',
    description:
      '查询服务器当前会话已选学生在已选考试中的逐题成绩、题型小计和数据完整度。' +
      '可指定 question_no；没有选定学生时会明确报错，不能用参数指定其他学生。',
    parameters: {
      type: 'object',
      properties: { question_no: { type: 'string', description: '题号，可选' } },
      required: [],
    },
  },
  {
    name: 'get_formal_attachment',
    title: '正式附件读取',
    description:
      '读取教师已确认的正式资料（仅当前会话、当前学期、已确认、文件存在）。' +
      '省略参数时返回正文摘要（PDF 保留首页/表格页/末页并标注省略页）；' +
      '仅在成绩分析数据包缺少明确细节时定向补读；每回合最多两次。',
    parameters: {
      type: 'object',
      properties: {
        title_keyword: { type: 'string', description: '按标题关键字过滤（可选）' },
        page: { type: 'integer', description: '指定读取 PDF 的页码（从 1 开始，可选）' },
        offset: { type: 'integer', description: '正文起始偏移（字符数，可选，配合 limit 分段）' },
        limit: { type: 'integer', description: '本次读取的字符数上限（默认 3000，最多 6000）' },
      },
      required: [],
    },
  },
  {
    name: 'get_teaching_guidance',
    title: '教学依据精确查询',
    description:
      '按考点 ID / 条目 ID / 标签精确查询内置教学知识库（课标要求、题型诊断映射、' +
      '错因与干预），返回单条短片段与可信度。仅当分析包信息不足以支撑某个具体结论时' +
      '使用，整个 run 最多两次；不做开放模糊检索。',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: '考点 ID、条目 ID 或标签，如 reading.inference / 宾语从句',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'submit_report',
    title: '报告提交',
    description:
      '提交最终结构化教学报告；每项 finding 必须引用至少一个本回合工具返回的 evidence ID。',
    parameters: {
      type: 'object',
      properties: {
        summary: { type: 'string', description: '报告摘要' },
        profile_summary: {
          type: 'string',
          description: '学生画像摘要：结合既有画像和本次证据重写的自然语言段落（仅学生诊断使用）',
        },
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              title: { type: 'string' },
              detail: { type: 'string' },
              evidence_ids: { type: 'array', items: { type: 'string' } },
            },
            required: ['title', 'evidence_ids'],
          },
        },
        recommendations: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              title: { type: 'string', description: '建议标题' },
              action: { type: 'string', description: '建议的做法' },
              rationale: { type: 'string', description: '提出建议的理由' },
              supports: {
                type: 'array',
                items: { type: 'string' },
                description: '支撑本建议的本回合证据 ID 列表（证据校验强制非空）',
              },
            },
            required: ['title', 'supports'],
          },
        },
      },
      required: ['findings', 'recommendations'],
    },
  },
]

const EXEC_TIMEOUT_MS = 20_000


import { readdirSync } from 'node:fs'

function scopeFilePath(scopeRoot, exec) {
  const sid = exec?.agent?.sessionId
  if (!sid) {
    return null
  }
  return path.join(scopeRoot, `${sid}.json`)
}

const resolveFilePath = scopeFilePath

// resolveScopeFile：优先 exec.agent.sessionId → 精确文件；拿不到 sessionId 时
// 退化为「目录内唯一活动 scope 文件」（服务器串行回合：回合前写、终态清除，
// 因此单文件即当前回合）。多文件/零文件一律 fail-closed，绝不串用 scope。
function resolveActiveScopeFile(scopeRoot, exec) {
  const bySid = scopeFilePath(scopeRoot, exec)
  if (bySid && scopeExists(bySid)) {
    return bySid
  }
  let files = []
  try {
    files = readdirSync(scopeRoot).filter((f) => f.endsWith('.json') && !f.startsWith('.'))
  } catch (e) {
    return null
  }
  if (files.length !== 1) {
    return null
  }
  const candidate = path.join(scopeRoot, files[0])
  return scopeExists(candidate) ? candidate : null
}


function scopeExists(scopeFilePath) {
  if (!scopeFilePath) return false
  try {
    readFileSync(scopeFilePath, 'utf8')
    return true
  } catch {
    return false
  }
}


// 读取 scope 中的能力与工具策略（v3 分析包路径）。
// tool_policy=null 表示旧服务器不限工具；空数组表示该 run 不允许任何工具。
function readScopePolicy(scopeFile) {
  try {
    const data = JSON.parse(readFileSync(scopeFile, 'utf8'))
    return {
      capability: typeof data.capability === 'string' ? data.capability : null,
      toolPolicy: Array.isArray(data.tool_policy)
        ? data.tool_policy.map(String)
        : null,
    }
  } catch {
    return { capability: null, toolPolicy: null }
  }
}


function runBridge(config, tool, scopeFile, args) {
  return new Promise((resolve, reject) => {
    const py = config.pythonBin || process.env.DSH_BRIDGE_PYTHON || 'python3'
    const moduleName = config.bridgeModule || 'backend.app.agent.education_bridge.bridge_cli'
    const dataDir = config.dataDir || process.env.DSH_EDUCATION_DATA_DIR || config.scopeRoot
    const argv = [
      '-m', moduleName,
      '--tool', tool,
      '--data-dir', dataDir,
      '--scope', scopeFile,
      '--args', JSON.stringify(args ?? {}),
    ]
    execFile(
      py,
      argv,
      { timeout: EXEC_TIMEOUT_MS, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err) {
          reject(new Error(`[education-bridge] 桥接进程失败: ${err.message}`))
          return
        }
        try {
          resolve(JSON.parse(stdout))
        } catch {
          reject(new Error('[education-bridge] 桥接输出无法解析（内部错误）'))
        }
      },
    )
  })
}


export const apply = (ctx, config) => {
  const scopeRoot = config?.scopeRoot || process.env.DSH_EDUCATION_SCOPE_ROOT
  if (!scopeRoot) {
    throw new Error('education-bridge: 缺少 scopeRoot 配置（DSH_EDUCATION_SCOPE_ROOT）')
  }

  for (const def of TOOL_DEFS) {
    ctx.tools.register({
      name: def.name,
      title: def.title,
      description: def.description,
      parameters: def.parameters,
      output: {
        schema: {
          type: 'object',
          properties: {
            ok: { type: 'boolean' },
            data: { type: 'object' },
            evidence_id: { type: 'string' },
            facts: { type: 'array' },
            notes: { type: 'array' },
          },
          required: ['ok', 'data'],
        },
        render: (args, value) => [{ type: 'text', text: JSON.stringify(value) }],
        presentationMeta: (args, value) => ({ tool: def.name }),
      },
      async execute(args, exec) {
        // fail-closed：scope 文件缺失（服务器未注入）→ 拒绝所有数据工具
        const scopeFile = resolveActiveScopeFile(scopeRoot, exec)
        if (!scopeFile || !scopeExists(scopeFile)) {
          throw new Error(
            `[education-bridge] 缺少服务器注入的 scope（当前无活动 scope 文件），` +
            `工具 ${def.name} 被拒绝（fail-closed）`
          )
        }
        // v3 策略预检（在 bridge_cli 之前拦截，省一次子进程）：
        // general_chat 允许少量安全只读工具；packet 模式下策略外工具直接拒绝。
        const { capability, toolPolicy } = readScopePolicy(scopeFile)
        if (toolPolicy && !toolPolicy.includes(def.name)) {
          throw new Error(
            `[education-bridge] 当前运行的工具策略不允许调用 ${def.name}` +
            `（允许: ${toolPolicy.join(', ')}）；标准路径请直接依据分析包调用 submit_report`
          )
        }
        return runBridge({ scopeRoot, ...config }, def.name, scopeFile, args)
      },
    })
  }
}

export default { apply, name: 'education-bridge' }
