# @deepseek-ai/dsh-client-ui-education

TeachMate's blank-session welcome panel and explicit context selector. The welcome cards seed a reviewable prompt in the composer; the selector occupies the conversation input dock and sends the teacher's selected term, exam, and student ids through the host-only `/teachmate-context` command. The model cannot set this context through a tool call, and the command does not record raw ids in the session log.

## Model Experience

### Context selection

#### What the model sees

The browser control changes the host-owned education scope. The model sees only the resulting `education_context`, `read_exam_overview`, and `read_student_profile` tool states, never the selector's raw form fields.

#### Token effect

The selector itself adds no model tokens. A successful selection changes the facts returned by subsequent education tools.

#### KV Cache effect

Changing context does not rewrite prior turns; it changes the host-owned scope used by later tool calls.

## Known Limitations and Deferred Work

- The first selector accepts numeric ids; a future WorkBench Remote will provide teacher-friendly searchable term, exam, and student names without exposing identifiers to the model.
