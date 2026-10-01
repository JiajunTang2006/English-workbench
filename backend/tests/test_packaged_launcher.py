"""Frozen desktop tools must return bridge JSON and select the current SDK."""
import sys
import pytest
import blank_launcher
import teachmate_runtime
from backend.app.agent.runtime import harness_runtime


def test_desktop_bridge_dispatch_does_not_open_or_configure_the_gui(monkeypatch):
    from backend.app.agent.education_bridge import bridge_cli
    calls = []
    monkeypatch.setattr(sys, 'argv', ['EnglishWorkBench', '-m', bridge_cli.__name__, '--help'])
    monkeypatch.setattr(bridge_cli, 'main', lambda args: calls.append(args))
    monkeypatch.setattr(blank_launcher, 'configure_blank_data_dir', lambda: pytest.fail('must not create a GUI data directory'))
    assert blank_launcher.main() == 0
    assert calls == [['--help']]


def test_desktop_bridge_rejects_arbitrary_modules(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['EnglishWorkBench', '-m', 'os'])
    assert blank_launcher.main() == 2


def test_sdk_readiness_uses_actual_runtime_not_removed_headless_profile(monkeypatch):
    monkeypatch.setattr(harness_runtime, 'locate_runtime_launch', lambda: ('/bundle/runtime/bin.js', None, ''))
    monkeypatch.setattr(teachmate_runtime, 'find_node', lambda: '/bundle/node')
    monkeypatch.setattr(teachmate_runtime, 'harness_headless_patch', lambda: pytest.fail('obsolete profile'))
    assert teachmate_runtime.harness_sdk_ready()
    monkeypatch.setattr(harness_runtime, 'locate_runtime_launch', lambda: ('', None, ''))
    assert not teachmate_runtime.harness_sdk_ready()


def test_sdk_config_launches_javascript_with_selected_node(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(harness_runtime, 'locate_runtime_launch', lambda: ('/bundle/bin.js', None, ''))
    monkeypatch.setattr(harness_runtime, 'select_agent_cordis', lambda data: '/bundle/teachmate.yml')
    monkeypatch.setattr(teachmate_runtime, 'find_node', lambda: '/bundle/node')
    settings = SimpleNamespace(data_dir=tmp_path)
    agent = SimpleNamespace(text_api_key='sk-test', text_model_name='deepseek-chat', text_api_base_url='http://127.0.0.1:9/v1')
    cfg = harness_runtime.build_harness_config(settings, agent)
    assert cfg.launch_args_override == ('/bundle/node', '/bundle/bin.js')


def test_runtime_builder_includes_headless_sdk_external_dependencies(monkeypatch, tmp_path):
    from tools import build_teachmate_runtime as builder
    source = tmp_path / 'upstream'
    target = tmp_path / 'runtime'
    import json
    owners = [('packages/session/session-title', 'zod'), ('packages/llm/llm-deepseek', 'eventsource-parser')]
    monkeypatch.setattr(builder, 'SDK_RUNTIME_PACKAGES', ('session-title', 'llm-deepseek'))
    for owner, name in owners:
        owner_dir = source / owner
        owner_dir.mkdir(parents=True)
        (owner_dir / 'package.json').write_text(json.dumps({'name': '@deepseek-ai/' + owner_dir.name, 'dependencies': {name: '^1.0.0'}}))
        dependency = source / owner / 'node_modules' / name
        dependency.mkdir(parents=True)
        (dependency / 'package.json').write_text(json.dumps({'name': name, 'version': '1.0.0'}))
    monkeypatch.setattr(builder, 'UPSTREAM', source)
    assert builder.include_sdk_external_dependencies(target) == 2
    assert builder.include_sdk_external_dependencies(target) == 0
    assert (target / owners[0][0] / 'node_modules/zod/package.json').is_file()


def test_runtime_builder_rejects_missing_sdk_dependency(monkeypatch, tmp_path):
    from tools import build_teachmate_runtime as builder
    monkeypatch.setattr(builder, 'UPSTREAM', tmp_path / 'missing')
    owner = tmp_path / 'missing/packages/session-title'
    owner.mkdir(parents=True)
    (owner / 'package.json').write_text('{"name":"@deepseek-ai/session-title","dependencies":{"zod":"^1.0.0"}}')
    monkeypatch.setattr(builder, 'SDK_RUNTIME_PACKAGES', ('session-title',))
    with pytest.raises(RuntimeError, match='SDK production dependency missing'):
        builder.include_sdk_external_dependencies(tmp_path / 'runtime')


def test_runtime_builder_preserves_transitive_external_dependency(monkeypatch, tmp_path):
    import json
    from tools import build_teachmate_runtime as builder
    source = tmp_path / 'upstream'
    owner = source / 'packages/entry'
    dep = owner / 'node_modules/chokidar'
    child = dep / 'node_modules/readdirp'
    for directory, data in [(owner, {'name': '@deepseek-ai/entry', 'dependencies': {'chokidar': '^4.0.0'}}), (dep, {'name': 'chokidar', 'version': '4.0.0', 'dependencies': {'readdirp': '^4.0.0'}}), (child, {'name': 'readdirp', 'version': '4.0.0'})]:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'package.json').write_text(json.dumps(data))
    monkeypatch.setattr(builder, 'UPSTREAM', source)
    monkeypatch.setattr(builder, 'SDK_RUNTIME_PACKAGES', ('entry',))
    target = tmp_path / 'runtime'
    assert builder.include_sdk_external_dependencies(target) == 2
    assert (target / 'packages/entry/node_modules/chokidar/node_modules/readdirp/package.json').is_file()
    builder.validate_relocatable_links(target)
