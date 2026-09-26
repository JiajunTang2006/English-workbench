from __future__ import annotations

import hashlib

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
