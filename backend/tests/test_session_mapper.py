"""
test_session_mapper — U1-03 会话映射测试
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.app.agent.session_mapper import (
    generate_harness_session_id,
    assign_harness_session,
    bump_context_revision,
    get_harness_session_id,
    validate_harness_session_id,
)
from backend.app.models.agent_entities import AgentSession


# ---------------------------------------------------------------------------
# generate_harness_session_id
# ---------------------------------------------------------------------------

class TestGenerateHarnessSessionId:
    def test_format(self):
        hid = generate_harness_session_id(42)
        assert hid.startswith("tm-42-")
        suffix = hid.split("tm-42-")[1]
        assert len(suffix) == 8
        int(suffix, 16)  # 是 hex

    def test_no_sensitive_info(self):
        """不包含学生姓名/班级名/学号。"""
        hid = generate_harness_session_id(99)
        # 只有 tm-{数字}-{hex} 格式
        assert hid.startswith("tm-99-")
        assert "-" in hid
        # 不包含中文
        assert all(ord(c) < 128 for c in hid)

    def test_uniqueness(self):
        """每次生成不同后缀。"""
        ids = {generate_harness_session_id(1) for _ in range(100)}
        assert len(ids) > 90  # 极高概率不重复


# ---------------------------------------------------------------------------
# validate_harness_session_id
# ---------------------------------------------------------------------------

class TestValidateHarnessSessionId:
    def test_valid_id(self):
        hid = generate_harness_session_id(5)
        assert validate_harness_session_id(hid) is True

    def test_valid_manual_id(self):
        assert validate_harness_session_id("tm-1-abcd1234") is True

    def test_no_prefix(self):
        assert validate_harness_session_id("1-abcd1234") is False

    def test_wrong_prefix(self):
        assert validate_harness_session_id("xx-1-abcd1234") is False

    def test_no_dash(self):
        assert validate_harness_session_id("tm-1abcd1234") is False

    def test_non_numeric_session(self):
        assert validate_harness_session_id("tm-abc-12345678") is False

    def test_non_hex_suffix(self):
        assert validate_harness_session_id("tm-1-xyzxyzxy") is False

    def test_empty(self):
        assert validate_harness_session_id("") is False
        assert validate_harness_session_id(None) is False

    def test_too_long_suffix(self):
        assert validate_harness_session_id("tm-1-" + "a" * 20) is False


# ---------------------------------------------------------------------------
# assign_harness_session (mocked DB)
# ---------------------------------------------------------------------------

class TestAssignHarnessSession:
    def _mock_session(self, existing_id=None):
        session = MagicMock()
        session.id = 1
        session.harness_session_id = existing_id
        session.context_revision = 0
        return session

    def test_assign_new(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = self._mock_session()
        result = assign_harness_session(db, 1)
        assert result.startswith("tm-1-")
        assert validate_harness_session_id(result)

    def test_assign_returns_existing(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = self._mock_session(
            existing_id="tm-1-abcd1234"
        )
        result = assign_harness_session(db, 1)
        assert result == "tm-1-abcd1234"

    def test_assign_nonexistent_session(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        with pytest.raises(ValueError, match="not found"):
            assign_harness_session(db, 999)


# ---------------------------------------------------------------------------
# bump_context_revision (mocked DB)
# ---------------------------------------------------------------------------

class TestBumpContextRevision:
    def test_bump_increments(self):
        db = MagicMock()
        session = MagicMock()
        session.context_revision = 3
        db.query.return_value.filter_by.return_value.first.return_value = session
        result = bump_context_revision(db, 1)
        assert result == 4
        assert session.context_revision == 4

    def test_bump_from_zero(self):
        db = MagicMock()
        session = MagicMock()
        session.context_revision = 0
        db.query.return_value.filter_by.return_value.first.return_value = session
        result = bump_context_revision(db, 1)
        assert result == 1

    def test_bump_from_none(self):
        db = MagicMock()
        session = MagicMock()
        session.context_revision = None
        db.query.return_value.filter_by.return_value.first.return_value = session
        result = bump_context_revision(db, 1)
        assert result == 1

    def test_bump_nonexistent(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        with pytest.raises(ValueError, match="not found"):
            bump_context_revision(db, 999)


# ---------------------------------------------------------------------------
# get_harness_session_id (mocked DB)
# ---------------------------------------------------------------------------

class TestGetHarnessSessionId:
    def test_get_existing(self):
        db = MagicMock()
        session = MagicMock()
        session.harness_session_id = "tm-1-abcd1234"
        db.query.return_value.filter_by.return_value.first.return_value = session
        assert get_harness_session_id(db, 1) == "tm-1-abcd1234"

    def test_get_none(self):
        db = MagicMock()
        session = MagicMock()
        session.harness_session_id = None
        db.query.return_value.filter_by.return_value.first.return_value = session
        assert get_harness_session_id(db, 1) is None

    def test_get_nonexistent(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        assert get_harness_session_id(db, 999) is None
