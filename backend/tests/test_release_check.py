from __future__ import annotations

import hashlib

import pytest

from tools import release_check


def test_release_manifest_excludes_itself(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("release content\n", encoding="utf-8")
    manifest = tmp_path / "release-manifest.sha256"

    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    monkeypatch.setattr(release_check, "_tracked_paths", lambda: [source, manifest])
    release_check.failures.clear()

    release_check.generate_manifest()

    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest.read_text(encoding="utf-8") == f"{expected_hash}  source.txt\n"
    assert "release-manifest.sha256" not in manifest.read_text(encoding="utf-8")


def test_sensitive_scan_accepts_decimal_timestamps(tmp_path, monkeypatch):
    source = tmp_path / "timings.json"
    source.write_text('{"start":274.13712345678,"end":275.15812345678}\n', encoding="utf-8")
    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    monkeypatch.setattr(release_check, "_tracked_paths", lambda: [source])
    monkeypatch.setattr(release_check, "failures", [])

    release_check.check_sensitive_files()
    assert not release_check.failures


def test_sensitive_scan_still_rejects_phone_after_decimal_timestamp(tmp_path, monkeypatch, capsys):
    source = tmp_path / "contact.json"
    phone = "137" + "12345678"
    source.write_text('{"start":274.13712345678,"phone":"' + phone + '"}\n', encoding="utf-8")
    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    monkeypatch.setattr(release_check, "_tracked_paths", lambda: [source])
    monkeypatch.setattr(release_check, "failures", [])

    with pytest.raises(SystemExit):
        release_check.check_sensitive_files()
    output = capsys.readouterr().out
    assert "疑似手机号: contact.json" in output
    assert phone not in output
