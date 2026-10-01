"""Copy compatible demo records into an empty competition installation.

Prepare an isolated database first; apply only after closing the desktop app.
Never copies credentials, AI conversations, sync settings, or schema versions.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


TABLES = (
    'terms', 'classes', 'students', 'enrollments', 'exams', 'exam_scores',
    'workspace_states', 'exam_paper_versions', 'exam_questions',
    'student_item_results', 'growth_rule_versions', 'growth_events',
    'growth_awards', 'student_growth_snapshots', 'growth_term_rules',
)


def connect_readonly(path: Path) -> sqlite3.Connection:
    # Immutable is safe only for a closed source with no pending WAL.
    wal = path.with_name(path.name + '-wal')
    if wal.exists() and wal.stat().st_size:
        return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    return sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)


def check_empty(connection: sqlite3.Connection) -> None:
    for table in TABLES:
        if table in ('terms', 'workspace_states', 'growth_rule_versions', 'growth_term_rules'):
            continue
        if connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
            raise ValueError(f'Target is not empty: {table}; import cancelled')
    states = connection.execute('SELECT state_json FROM workspace_states').fetchall()
    for (raw,) in states:
        state = json.loads(raw)
        for key in ('students', 'archivedStudents', 'exams', 'archivedExams',
                    'todos', 'writings', 'recitations', 'homeworkTasks', 'paperDocuments'):
            if state.get(key):
                raise ValueError(f'Target workspace contains {key}; import cancelled')
    if connection.execute('SELECT COUNT(*) FROM terms').fetchone()[0] != 1:
        raise ValueError('Expected exactly one empty initial term')


def validate(connection: sqlite3.Connection) -> dict:
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    counts = {t: connection.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
              for t in TABLES}
    state = json.loads(connection.execute('SELECT state_json FROM workspace_states').fetchone()[0])
    assert len(state['students']) == counts['students']
    assert len(state['exams']) == counts['exams']
    assert all(s['name'].endswith('同学') for s in state['students'])
    assert counts['students'] > 0
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target, output = args.target.resolve(), args.output.resolve()
    if args.apply:
        if (target.parent / '.desktop-activate.sock').exists():
            raise ValueError('Close the competition app before applying the import')
        with connect_readonly(output) as prepared:
            counts = validate(prepared)
            with sqlite3.connect(target) as destination:
                check_empty(destination)
                assert destination.execute('SELECT * FROM alembic_version').fetchall() == prepared.execute('SELECT * FROM alembic_version').fetchall()
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup = output.parent / f'before-import-{stamp}.db'
                with sqlite3.connect(backup) as saved:
                    destination.backup(saved)
                prepared.backup(destination)
                assert validate(destination) == counts
        print(json.dumps({'imported': counts, 'backup': str(backup)}, ensure_ascii=False))
        return
    if not args.source or output.exists() or output == target:
        raise ValueError('Specify a source and a new output file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with connect_readonly(target) as existing, connect_readonly(args.source.resolve()) as source:
        check_empty(existing)
        with sqlite3.connect(output) as prepared:
            existing.backup(prepared)
            prepared.execute('PRAGMA foreign_keys=OFF')
            for table in TABLES:
                columns = [r[1] for r in prepared.execute(f'PRAGMA table_info("{table}")')]
                assert columns == [r[1] for r in source.execute(f'PRAGMA table_info("{table}")')], table
                fields = ','.join(f'"{c}"' for c in columns)
                rows = source.execute(f'SELECT {fields} FROM "{table}"').fetchall()
                prepared.execute(f'DELETE FROM "{table}"')
                prepared.executemany(f'INSERT INTO "{table}" ({fields}) VALUES ({",".join("?" for _ in columns)})', rows)
            term_id = prepared.execute('SELECT id FROM terms').fetchone()[0]
            prepared.execute("UPDATE app_settings SET value_json=? WHERE key='active_term_id'", (json.dumps(term_id),))
            counts = validate(prepared)
        with connect_readonly(output) as prepared:
            assert validate(prepared) == counts
            state = json.loads(prepared.execute('SELECT state_json FROM workspace_states').fetchone()[0])
            output.with_suffix('.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': str(output), 'counts': counts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
