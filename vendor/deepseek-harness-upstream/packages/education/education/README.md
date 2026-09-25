# dsh-education

The TeachMate education plugin. It exposes a small, read-only model-facing tool set and a loopback Bridge to the existing WorkBench API.

The plugin supports two modes:

- `mock` (default): deterministic data for Harness development and keyless smoke tests.
- `bridge`: calls the local WorkBench API with a loopback bearer token. Scope is supplied by the host configuration, never by model tool arguments.

The first slice intentionally returns teaching reports as drafts. Persisting a teacher-confirmed report is a later phase and must not be implemented by silently writing model output to the formal evaluation tables.

The host also exposes the explicit `/teachmate-context` command. The TeachMate browser surface uses it to update the selected scope; raw command input is not recorded in the session log.

## Model Experience

### Education policy

#### What the model sees

The `education:policy` system-prompt section identifies TeachMate as a teaching assistant, requires evidence and uncertainty, and says that every report is a teacher-confirmed draft. The section is installed at order 90.

#### Token effect

The policy is a short stable prefix added to each assembled request while the education plugin is mounted.

#### KV Cache effect

The policy is unchanged for a given preset, so it remains cacheable across turns; changing presets changes the prompt prefix.

### Education tools

#### What the model sees

The model sees `education_context`, `read_exam_overview`, `read_student_profile`, and `submit_teaching_report`. Raw student identifiers, names, phone numbers, file paths, and database ids are not included in tool output. The tool schemas are also visible in the generated [tool catalog](../../../docs/tool-catalog.md).

#### Token effect

Tool schemas are included by the normal Harness tool presentation mode. Results contain only bounded aggregate facts, sanitized score history, or a structured draft; the Bridge caps decoded responses at `maxResponseBytes`.

#### KV Cache effect

The tool catalog stays stable for a mounted TeachMate preset. Tool calls and results append ordinary session content and therefore extend, but do not rewrite, the reusable prompt prefix.

## Known Limitations and Deferred Work

- The mock provider is deterministic and is intended for local development, not production data.
- Bridge mode requires the host to supply a selected term/exam/student scope; the planned context-selection UI is not part of this first slice.
- `submit_teaching_report` never persists a formal evaluation. A later WorkBench confirmation flow must explicitly save teacher-approved drafts.
