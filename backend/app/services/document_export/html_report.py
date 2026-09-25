"""L4 自包含 HTML 报告生成（零依赖）。

生成完全内联样式、可离线打开、可交给 weasyprint 转 PDF 的单文件 HTML。
内容只来自已确认的结构化答案（StructuredAnswer），不渲染未确认模型输出。
设计要点：
- 中文字体回退到系统常见中文字族（macOS/Windows 均覆盖），不内嵌字体二进制；
- 章节、段落、列表、表格、证据附录齐全；
- 颜色与现有 TeachMate Miro 风格中性灰一致，但通过内联 CSS 独立承载，不依赖前端运行时。
"""

from __future__ import annotations

import html
from typing import Any


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _fmt_confidence(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return _esc(value)


def _render_findings(findings: list[dict]) -> str:
    if not findings:
        return ""
    items = []
    for idx, f in enumerate(findings, 1):
        title = _esc(f.get("title") or f"发现 {idx}")
        claim = _esc(f.get("description") or f.get("claim") or "")
        conf = _fmt_confidence(f.get("confidence")) if f.get("confidence") else ""
        badge = f'<span class="badge">{conf}</span>' if conf else ""
        eids = f.get("evidence_ids") or []
        refs = ""
        if eids:
            refs = (
                '<div class="evidence">证据：'
                + "、".join(f'<span class="evid">{_esc(e)}</span>' for e in eids)
                + "</div>"
            )
        items.append(
            f'<article class="finding"><div class="fhead">'
            f'<span class="findex">{idx}</span><strong>{title}</strong>{badge}</div>'
            f'<p class="fclaim">{claim}</p>{refs}</article>'
        )
    return (
        '<section class="block"><h2><span class="ico">🔍</span>主要发现'
        f'<span class="count">{len(findings)}</span></h2>'
        '<div class="finding-list">' + "".join(items) + "</div></section>"
    )


def _render_recommendations(recs: list[dict]) -> str:
    if not recs:
        return ""
    items = []
    for r in sorted(recs, key=lambda x: (x.get("priority") if isinstance(x.get("priority"), int) else 99)):
        action = _esc(r.get("action") or "")
        rationale = _esc(r.get("rationale") or "")
        priority = _esc(r.get("priority") if r.get("priority") is not None else 3)
        supports = r.get("supports") or []
        sup = f'<small>依据：{"、".join(_esc(s) for s in supports)}</small>' if supports else ""
        items.append(
            f'<li class="rec"><div class="rpri">P{priority}</div>'
            f'<div class="rcopy"><strong>{action}</strong>'
            + (f"<p>{rationale}</p>" if rationale else "")
            + (sup if sup else "")
            + "</div></li>"
        )
    return (
        '<section class="block"><h2><span class="ico">💡</span>行动建议'
        f'<span class="count">{len(recs)}</span></h2>'
        f'<ol class="rec-list">{"".join(items)}</ol></section>'
    )


def _render_limitations(items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return (
        '<section class="block limitations"><h2><span class="ico">⚠️</span>局限与注意事项</h2>'
        f"<ul class=\"struct-list\">{lis}</ul></section>"
    )


def _render_sections(sections: list[dict]) -> str:
    if not sections:
        return ""
    out = []
    for s in sections:
        title = _esc(s.get("title") or "")
        body = _esc(s.get("body") or s.get("content") or "")
        if not (title or body):
            continue
        out.append(f'<section class="block"><h2>{title}</h2><p>{body}</p></section>')
    return "".join(out)


def _render_evidence_appendix(evidence: list[dict]) -> str:
    if not evidence:
        return ""
    rows = []
    for e in evidence:
        eid = _esc(e.get("evidence_id") or e.get("id") or "")
        etype = _esc(e.get("evidence_type") or "")
        summary = _esc(e.get("display_summary") or e.get("summary") or "")
        source = _esc(e.get("source_file") or "")
        page = e.get("source_page")
        src = f"{source}" + (f" p.{page}" if isinstance(page, int) else "")
        rows.append(
            f"<tr><td class=\"mono\">{eid}</td><td>{etype}</td>"
            f"<td>{summary}</td><td>{src}</td></tr>"
        )
    return (
        '<section class="block appendix"><h2><span class="ico">📎</span>证据附录</h2>'
        '<table class="evtable"><thead><tr><th>证据 ID</th><th>类型</th>'
        "<th>说明</th><th>来源</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


_BASE_CSS = """
:root{
  --ink:#1c1c1e; --slate:#555a6a; --stone:#8e91a0;
  --surface:#ffffff; --soft:#fafbfc; --hair:#e0e2e8; --hair-strong:#c7cad5;
  --brand:#ffd02f; --brand-ink:#1c1c1e; --rose:#fde0f0; --teal:#d8fbf6;
  --accent:#4262ff;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
    "Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  color:var(--ink); background:var(--surface);
  line-height:1.6; font-size:14px;
}
.page{max-width:820px; margin:0 auto; padding:40px 44px;}
header.doc-head{
  border-bottom:3px solid var(--brand); padding-bottom:14px; margin-bottom:24px;
  display:flex; align-items:center; justify-content:space-between; gap:16px;
}
header.doc-head .title{font-size:22px; font-weight:700; margin:0;}
header.doc-head .meta{color:var(--slate); font-size:12px; text-align:right;}
.summary{
  background:var(--soft); border:1px solid var(--hair); border-radius:10px;
  padding:14px 18px; margin-bottom:22px;
}
.summary h2{margin:0 0 8px; font-size:15px;}
.block{margin-bottom:26px; page-break-inside:avoid;}
.block h2{font-size:16px; margin:0 0 10px; display:flex; align-items:center; gap:8px;}
.block h2 .ico{font-size:15px;}
.block h2 .count{
  font-size:12px; color:var(--slate); background:var(--soft);
  border:1px solid var(--hair); border-radius:999px; padding:0 8px; margin-left:4px;
}
.finding{border:1px solid var(--hair); border-radius:10px; padding:12px 14px; margin-bottom:10px;}
.fhead{display:flex; align-items:center; gap:8px; margin-bottom:4px;}
.findex{
  width:22px; height:22px; border-radius:50%; background:var(--brand-ink); color:#fff;
  display:inline-flex; align-items:center; justify-content:center; font-size:12px; font-weight:700;
}
.badge{font-size:11px; color:#7a5a00; background:var(--brand); border-radius:6px; padding:1px 6px;}
.fclaim{margin:2px 0 0; color:var(--ink);}
.evidence{margin-top:6px; font-size:12px; color:var(--slate);}
.evid{background:var(--soft); border:1px solid var(--hair); border-radius:5px; padding:0 5px; margin-right:3px;}
.rec-list{margin:0; padding-left:0; list-style:none; counter-reset:none;}
.rec{display:flex; gap:12px; border:1px solid var(--hair); border-radius:10px; padding:10px 14px; margin-bottom:8px;}
.rpri{
  flex:0 0 auto; width:34px; height:34px; border-radius:8px; background:var(--teal);
  color:var(--ink); display:inline-flex; align-items:center; justify-content:center; font-weight:700; font-size:13px;
}
.rcopy{flex:1;}
.rcopy p{margin:4px 0 0; color:var(--slate);}
.rcopy small{color:var(--stone);}
.struct-list,.lim-list{margin:0; padding-left:20px;}
.limitations{background:#fff7f2; border:1px solid #f3d3bf; border-radius:10px; padding:12px 18px;}
.evtable{width:100%; border-collapse:collapse; font-size:12.5px;}
.evtable th,.evtable td{border:1px solid var(--hair); padding:6px 8px; text-align:left; vertical-align:top;}
.evtable th{background:var(--soft); color:var(--slate);}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;}
footer.doc-foot{
  margin-top:30px; border-top:1px solid var(--hair); padding-top:10px;
  color:var(--stone); font-size:11px; display:flex; justify-content:space-between;
}
"""


def build_html_report(
    *,
    title: str,
    answer: dict[str, Any],
    evidence: list[dict] | None = None,
    meta: dict[str, str] | None = None,
    privacy_level: str = "confidential",
) -> str:
    """生成自包含 HTML 报告字符串。

    参数:
        title: 文档标题（来自 DocumentSpec.title）
        answer: StructuredAnswer 字典（summary/findings/recommendations/limitations/sections）
        evidence: 证据附录列表（来自 AnalysisEvidence.display_summary）
        meta: 页眉右侧元信息（如学期、班级、生成时间）
        privacy_level: 隐私等级标记（confidential/internal/public）
    """
    summary = _esc(answer.get("summary") or "")
    answer_type = _esc(answer.get("answer_type") or "teachmate-report")
    findings = answer.get("findings") or []
    recs = answer.get("recommendations") or []
    limits = answer.get("limitations") or []
    sections = answer.get("sections") or []

    meta_html = ""
    if meta:
        meta_html = "<br>".join(f"{_esc(k)}：{_esc(v)}" for k, v in meta.items())

    privacy_label = {"confidential": "机密·仅教师", "internal": "内部", "public": "可外发"}.get(
        privacy_level, privacy_level
    )

    body = (
        '<header class="doc-head"><div><h1 class="title">'
        + _esc(title or "TeachMate 教学分析报告")
        + f'</h1><div class="meta">类型：{answer_type}</div></div>'
        + (f'<div class="meta">{meta_html}</div>' if meta_html else "")
        + "</header>"
        + (f'<section class="summary"><h2>摘要</h2><p>{summary}</p></section>' if summary else "")
        + _render_findings(findings)
        + _render_recommendations(recs)
        + _render_sections(sections)
        + _render_limitations(limits)
        + _render_evidence_appendix(evidence or [])
    )

    foot = (
        '<footer class="doc-foot"><span>由 TeachMate 本地生成 · 隐私等级：'
        f"{_esc(privacy_label)}</span><span>DocumentSpec v1</span></footer>"
    )

    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title or 'TeachMate 教学分析报告')}</title>"
        f"<style>{_BASE_CSS}</style></head><body><div class=\"page\">{body}{foot}</div></body></html>"
    )
