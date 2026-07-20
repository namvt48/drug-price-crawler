"""Tests cho utils.trial_manager — controls DPC_DEV explicitly (no fixture env)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import utils.trial_manager as trial_manager
from utils.trial_manager import TrialManager


@pytest.fixture
def dev_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force _DEV_OVERRIDE=False regardless of env."""
    monkeypatch.setattr(trial_manager, "_DEV_OVERRIDE", False)


@pytest.fixture
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect TrialManager._storage_paths to a tmp file."""
    monkeypatch.setattr(
        TrialManager, "_storage_paths", staticmethod(lambda: [tmp_path / "t.dat"])
    )


class TestEncryptDecrypt:
    def test_roundtrip(self) -> None:
        data = {"first_run": "2026-01-01T00:00:00", "machine_id": "abc"}
        raw = TrialManager._encrypt(data)
        assert isinstance(raw, bytes)
        back = TrialManager._decrypt(raw)
        assert back == data

    def test_decrypt_garbage_returns_none(self) -> None:
        assert TrialManager._decrypt(b"garbage-not-base64!!") is None

    def test_decrypt_empty_returns_none(self) -> None:
        assert TrialManager._decrypt(b"") is None


class TestTrialCheck:
    def test_first_run_starts_fourteen_day_trial(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage, tmp_path: Path
    ) -> None:
        # Given: a machine with no existing trial state.
        tm = TrialManager()

        # When: the app checks the trial for the first time.
        status = tm.check()

        # Then: the full fourteen-day allowance is available and persisted.
        assert status.is_valid is True
        assert status.is_first_run is True
        assert status.days_remaining == 14
        assert (tmp_path / "t.dat").exists()

    def test_second_run_not_first(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage
    ) -> None:
        tm = TrialManager()
        tm.check()  # first
        tm2 = TrialManager()
        status = tm2.check()
        assert status.is_valid is True
        assert status.is_first_run is False

    def test_expired(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage
    ) -> None:
        tm = TrialManager()
        old = (datetime.now() - timedelta(days=15)).isoformat()
        tm._write_trial({"first_run": old, "machine_id": tm._machine_id})
        status = tm.check()
        assert status.is_valid is False
        assert status.days_remaining == 0

    def test_eight_day_old_trial_remains_valid(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage
    ) -> None:
        # Given: an installation first opened eight days ago.
        tm = TrialManager()
        first_run = (datetime.now() - timedelta(days=8)).isoformat()
        tm._write_trial({"first_run": first_run, "machine_id": tm._machine_id})

        # When: the app checks the extended trial.
        status = tm.check()

        # Then: the installation remains usable inside the fourteen-day window.
        assert status.is_valid is True

    def test_machine_id_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage
    ) -> None:
        # _read_trial filters out mismatched machine_ids, so to test the
        # mismatch branch in check() we monkeypatch _read_trial directly.
        tm = TrialManager()
        monkeypatch.setattr(
            tm,
            "_read_trial",
            lambda: {
                "first_run": datetime.now().isoformat(),
                "machine_id": "other-machine",
            },
        )
        status = tm.check()
        assert status.is_valid is False

    def test_dev_override_always_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(trial_manager, "_DEV_OVERRIDE", True)
        # Don't need isolated storage — check returns early.
        tm = TrialManager()
        status = tm.check()
        assert status.is_valid is True
        assert "Dev mode" in status.message


class TestUnlimitedLicense:
    def test_disabled_by_default_ignores_valid_key(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            TrialManager, "_license_paths", staticmethod(lambda: [tmp_path / "license.key"])
        )
        tm = TrialManager()
        (tmp_path / "license.key").write_text(
            TrialManager.generate_license_token(tm._machine_id)
        )
        status = tm.check()
        # UNLIMITED_LICENSE_ENABLED is False — falls through to normal trial flow.
        assert status.days_remaining != -1
        assert status.is_first_run is True

    def test_valid_token_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trial_manager, "UNLIMITED_LICENSE_ENABLED", True)
        monkeypatch.setattr(
            TrialManager, "_license_paths", staticmethod(lambda: [tmp_path / "license.key"])
        )
        tm = TrialManager()
        (tmp_path / "license.key").write_text(
            TrialManager.generate_license_token(tm._machine_id)
        )
        status = tm.check()
        assert status.is_valid is True
        assert status.days_remaining == -1
        assert "vĩnh viễn" in status.message

    def test_wrong_token_when_enabled_falls_back_to_trial(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trial_manager, "UNLIMITED_LICENSE_ENABLED", True)
        monkeypatch.setattr(
            TrialManager, "_license_paths", staticmethod(lambda: [tmp_path / "license.key"])
        )
        tm = TrialManager()
        (tmp_path / "license.key").write_text("not-the-right-token")
        status = tm.check()
        assert status.days_remaining != -1
        assert status.is_first_run is True

    def test_missing_license_file_when_enabled_falls_back_to_trial(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(trial_manager, "UNLIMITED_LICENSE_ENABLED", True)
        monkeypatch.setattr(
            TrialManager, "_license_paths", staticmethod(lambda: [tmp_path / "license.key"])
        )
        tm = TrialManager()
        status = tm.check()
        assert status.days_remaining != -1
        assert status.is_first_run is True

    def test_generate_token_deterministic_per_machine(self) -> None:
        a = TrialManager.generate_license_token("machine-a")
        b = TrialManager.generate_license_token("machine-a")
        c = TrialManager.generate_license_token("machine-b")
        assert a == b
        assert a != c


class TestComputeMachineId:
    def test_stable(self) -> None:
        a = TrialManager._compute_machine_id()
        b = TrialManager._compute_machine_id()
        assert a == b
        assert len(a) == 32


class TestReadTrial:
    def test_read_returns_none_when_no_file(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage, tmp_path: Path
    ) -> None:
        tm = TrialManager()
        assert tm._read_trial() is None

    def test_read_returns_data_after_write(
        self, monkeypatch: pytest.MonkeyPatch, dev_off, isolated_storage
    ) -> None:
        tm = TrialManager()
        data = {
            "first_run": datetime.now().isoformat(),
            "machine_id": tm._machine_id,
        }
        tm._write_trial(data)
        got = tm._read_trial()
        assert got is not None
        assert got["machine_id"] == tm._machine_id
