# TeachMate

This directory contains two Cordis compositions for TeachMate:

## 1. Web Profile (`cordis.yml`)

Selects the shipped `teachmate` agent preset over the normal Web composition.

Use `TEACHMATE_MODE=mock` for a keyless development run. Use `TEACHMATE_MODE=bridge` together with `WORKBENCH_TOKEN`, and add the host-selected scope through a local overlay once the context-selection UI is wired.

From the Harness checkout, build once and start the local Web profile:

```bash
pnpm run build
TEACHMATE_MODE=mock pnpm dsh web --patch examples/teachmate/cordis.yml
```

The overlay binds the Web server to `127.0.0.1:3082` and makes TeachMate the default agent preset for that profile.

## 2. JSON-RPC SDK Profile (`cordis.sdk.yml`)

A headless JSON-RPC composition for `dsh-sdk-jsonrpc-server`. This is the
production-safe runtime used by the TeachMate backend (Harness Manager) for
unattended agent sessions.

**Only the following components are included:**

| Component | Package | Purpose |
|---|---|---|
| `sdk-jsonrpc-server` | `@deepseek-ai/dsh-sdk-jsonrpc-server` | JSON-RPC server entry point |
| `llm-deepseek` | `@deepseek-ai/dsh-llm-deepseek` | DeepSeek model adapter |
| `persona` | `@deepseek-ai/dsh-persona` | TeachMate teaching assistant persona |
| `education` | `@deepseek-ai/dsh-education` | Education-only tools (exam stats, trends, evidence) |
| `sessions` | `@deepseek-ai/dsh-session-persistence-jsonl` | JSONL session persistence |
| `session-checkpoints` | `@deepseek-ai/dsh-session-checkpoint-policy` | Checkpoint policy for recovery |
| `token-meter` | `@deepseek-ai/dsh-token-meter` | Token usage tracking for cost auditing |
| `compaction-basic` | `@deepseek-ai/dsh-compaction-basic` | Context compaction |

**Explicitly excluded:** Shell, FS, LSP, Web, Subagent, Skill, Todo, code-runtime,
terminal, jobs, and any tool that can execute arbitrary code.

**stdout** is reserved exclusively for JSON-RPC frames; all logging goes to **stderr**.

Usage:

```bash
pnpm run build
DSH_CORDIS_CONFIG=examples/teachmate/cordis.sdk.yml \
TEACHMATE_MODE=mock \
node apps/cli/bin/dsh-jsonrpc.mjs
```
