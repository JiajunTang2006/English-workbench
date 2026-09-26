# L0 版本化协议契约（Protocol Schemas）

> 长期开发方案 L0 阶段交付物之一。将跨阶段交互的「协议」冻结为版本化 JSON Schema + 示例，
> 作为 L1–L4 各阶段实现与回归测试的唯一契约来源。
> 规则：协议字段**只增不删**；删除或重解释字段必须升大版本（v1 → v2）。

状态图例：**已实现** = 本轮已落地并可被测试覆盖；**已约定** = 本文件定义契约，实现留给对应阶段。

---

## 1. AttachmentUploadAPI v1  ★ 已实现（L1）

前端上传能力声明 + 多部件上传结果。对应 `GET /api/v1/attachments/capabilities` 与
`POST /api/v1/attachments/upload`。

### 1.1 capabilities 响应（`GET /capabilities`）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AttachmentUploadCapabilities",
  "version": "1.0",
  "type": "object",
  "properties": {
    "api_version":               { "type": "string", "const": "1.0" },
    "multipart_enabled":        { "type": "boolean", "description": "L1 多部件开关；false 时前端回退 Base64" },
    "multipart_endpoint":       { "type": "string", "const": "/api/v1/attachments/upload" },
    "legacy_base64_endpoint":   { "type": "string", "const": "/api/v1/attachments" },
    "max_attachment_bytes":     { "type": "integer", "minimum": 1 },
    "max_image_pixels":         { "type": "integer", "minimum": 1 },
    "max_pdf_pages":            { "type": "integer", "minimum": 1 },
    "max_concurrent_uploads":   { "type": "integer", "minimum": 1 },
    "chunk_size_bytes":         { "type": "integer", "minimum": 1 },
    "allowed_extensions":       { "type": "array", "items": { "type": "string", "pattern": "^\\.[a-z0-9]+$" } },
    "image_extensions":         { "type": "array", "items": { "type": "string" } },
    "accept_attribute":         { "type": "string", "description": "可直接用于 <input type=file accept>" },
    "extension_mime_map":       { "type": "object", "additionalProperties": { "type": "string" } },
    "supports_cancel":          { "type": "boolean", "const": true },
    "supports_progress":        { "type": "boolean", "const": true },
    "dedupe_scope":             { "type": "string", "const": "term_id+sha256" },
    "error_codes":              { "type": "array", "items": { "type": "string" } },
    "metrics":                  { "$ref": "#/definitions/UploadMetrics" }
  },
  "required": [
    "api_version", "multipart_enabled", "multipart_endpoint", "legacy_base64_endpoint",
    "max_attachment_bytes", "max_concurrent_uploads", "chunk_size_bytes",
    "allowed_extensions", "image_extensions", "accept_attribute",
    "supports_cancel", "supports_progress", "dedupe_scope", "error_codes"
  ],
  "definitions": {
    "UploadMetrics": {
      "type": "object",
      "properties": {
        "total": { "type": "integer" },
        "succeeded": { "type": "integer" },
        "failed": { "type": "integer" },
        "deduped": { "type": "integer" },
        "total_bytes": { "type": "integer" },
        "average_duration_ms": { "type": "integer" },
        "by_code": { "type": "object", "additionalProperties": { "type": "integer" } }
      }
    }
  }
}
```

**示例响应**
```json
{
  "api_version": "1.0",
  "multipart_enabled": false,
  "multipart_endpoint": "/api/v1/attachments/upload",
  "legacy_base64_endpoint": "/api/v1/attachments",
  "max_attachment_bytes": 52428800,
  "max_image_pixels": 33554432,
  "max_pdf_pages": 20,
  "max_concurrent_uploads": 3,
  "chunk_size_bytes": 1048576,
  "allowed_extensions": [".pdf", ".xlsx", ".csv", ".txt", ".md", ".docx", ".png", ".jpg", ".jpeg"],
  "image_extensions": [".png", ".jpg", ".jpeg"],
  "accept_attribute": ".pdf,.xlsx,.csv,.txt,.md,.docx,.png,.jpg,.jpeg",
  "extension_mime_map": { ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg" },
  "supports_cancel": true,
  "supports_progress": true,
  "dedupe_scope": "term_id+sha256",
  "error_codes": ["UPLOAD_EMPTY_FILE","UPLOAD_TOO_LARGE","UPLOAD_UNSUPPORTED_TYPE","UPLOAD_MIME_MISMATCH","UPLOAD_SIGNATURE_MISMATCH","UPLOAD_IMAGE_TOO_LARGE","UPLOAD_IMAGE_INVALID","UPLOAD_PDF_TOO_MANY_PAGES","UPLOAD_INTEGRITY_FAILED","UPLOAD_INVALID_PATH","UPLOAD_BUSY","UPLOAD_STORAGE_FAILED"]
}
```

### 1.2 错误响应（统一结构）

所有上传失败返回 `{"detail": {"code": "<UPLOAD_*>", "message": "<可读中文>"}}`，HTTP 状态：
413 超限 / 422 空文件 / 415 类型或签名不匹配 / 400 完整性或路径 / 429 并发超限。

### 1.3 去重契约
同学期（`term_id`）内相同 `sha256` 重复上传直接复用既有记录，响应 201 且 `id` 与首次一致；
前端据此做「幂等复用」提示，不重复解析。

---

## 2. TeachMatePluginAPI v1  ✶ 已约定（L2 实现）

Codex 只读插件：本地 MCP Server 暴露**只读**工具，配对令牌鉴权。本阶段只冻结契约。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TeachMatePluginManifest",
  "version": "1.0",
  "type": "object",
  "properties": {
    "schema_version": { "type": "string", "const": "1.0" },
    "name":           { "type": "string" },
    "mode":           { "type": "string", "enum": ["read_only"] },
    "auth": {
      "type": "object",
      "properties": {
        "type": { "type": "string", "const": "paired_token" },
        "token_env": { "type": "string", "description": "令牌所在环境变量名，不存储令牌本体" }
      },
      "required": ["type", "token_env"]
    },
    "tools": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "access": { "type": "string", "enum": ["read"] },
          "scope": { "type": "string", "enum": ["workspace", "term", "attachment"] }
        },
        "required": ["name", "access", "scope"]
      }
    },
    "endpoints": {
      "type": "object",
      "properties": {
        "mcp":   { "type": "string" },
        "health":{ "type": "string" }
      }
    }
  },
  "required": ["schema_version", "name", "mode", "auth", "tools"]
}
```

**示例清单**
```json
{
  "schema_version": "1.0",
  "name": "teachmate-codex-readonly",
  "mode": "read_only",
  "auth": { "type": "paired_token", "token_env": "TEACHMATE_PLUGIN_TOKEN" },
  "tools": [
    { "name": "list_attachments",  "access": "read", "scope": "term" },
    { "name": "read_attachment",    "access": "read", "scope": "attachment" },
    { "name": "list_sessions",      "access": "read", "scope": "workspace" }
  ],
  "endpoints": { "mcp": "http://127.0.0.1:8765/mcp", "health": "http://127.0.0.1:8765/health" }
}
```

**约束**：所有工具 `access` 必须为 `read`；任何写操作（create/update/delete）不得在 v1 暴露。
令牌只经环境变量传递，协议体/日志不出现令牌明文。

---

## 3. VisionResult v1  ✶ 已约定（L3 实现）

多模态视觉（OCR）结果。对应 `POST /api/v1/attachments/{id}/parse` 的图片解析产物。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "VisionResult",
  "version": "1.0",
  "type": "object",
  "properties": {
    "attachment_id": { "type": "integer" },
    "status": { "type": "string", "enum": ["pending_ocr", "parsed", "parse_failed"] },
    "engine": { "type": "string", "description": "视觉模型标识，不暴露密钥" },
    "language": { "type": "string" },
    "text": { "type": "string", "description": "OCR 提取的可读文字（UTF-8）" },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "pages": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "index": { "type": "integer" },
          "text": { "type": "string" },
          "blocks": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "text": { "type": "string" },
                "bbox": { "type": "array", "items": { "type": "number" }, "minItems": 4, "maxItems": 4 }
              }
            }
          }
        }
      }
    }
  },
  "required": ["attachment_id", "status"]
}
```

**示例**
```json
{
  "attachment_id": 42,
  "status": "parsed",
  "engine": "vision-ocr-v1",
  "language": "zh",
  "text": "听力和阅读各占 30 分……",
  "confidence": 0.94,
  "pages": [{ "index": 0, "text": "听力和阅读各占 30 分……", "blocks": [] }]
}
```

**约束**：`confidence` 为置信区间下界，缺省时不臆造；`bbox` 坐标归一化到 [0,1]。
未确认内容不得进入前端展示以外的任何持久化字段。

---

## 4. DocumentSpec v1  ★ 已实现（L4，本地 PDF/DOCX）

本地 PDF/DOCX 导出请求规范。对应 `POST /api/v1/documents/export`（L4 新增）。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DocumentSpec",
  "version": "1.0",
  "type": "object",
  "properties": {
    "format": { "type": "string", "enum": ["pdf", "docx"] },
    "template": { "type": "string", "enum": ["report", "worksheet", "summary"] },
    "title": { "type": "string" },
    "locale": { "type": "string", "const": "zh-CN" },
    "source": {
      "type": "object",
      "properties": {
        "session_ids": { "type": "array", "items": { "type": "integer" } },
        "attachment_ids": { "type": "array", "items": { "type": "integer" } }
      }
    },
    "provider": {
      "type": "object",
      "properties": {
        "type": { "type": "string", "enum": ["local", "remote"] },
        "remote_enabled": { "type": "boolean" }
      },
      "description": "remote 需先开 document_export_enabled 且 remote_document_provider_enabled（fail-closed）"
    },
    "options": { "type": "object", "additionalProperties": true }
  },
  "required": ["format", "template", "source"]
}
```

**示例**
```json
{
  "format": "pdf",
  "template": "report",
  "title": "初三(2)班 期中英语分析",
  "locale": "zh-CN",
  "source": { "session_ids": [11, 12], "attachment_ids": [42] },
  "provider": { "type": "local", "remote_enabled": false }
}
```

**约束**：`provider.type=remote` 仅在 `document_export_enabled` 与 `remote_document_provider_enabled`
同时开启时接受，否则返回 409（依赖未满足）。导出内容不含未确认模型输出。

---

## 5. 兼容性边界汇总

| 协议 | 大版本变更触发条件 | 当前状态 |
| --- | --- | --- |
| AttachmentUploadAPI | 删除/重解释 `error_codes`、`dedupe_scope`、端点路径 | v1.0 已实现 |
| TeachMatePluginAPI | 放开写操作、变更鉴权方式 | v1.0 已约定 |
| VisionResult | 变更 `status` 枚举、坐标体系 | v1.0 已约定 |
| DocumentSpec | 删除 `format`/`template`、放宽 provider 依赖 | v1.0 已实现 |
