"""Trial 7 ngày — local, không cần server.

Lưu ngày first-run + machine fingerprint vào 2 vị trí (cạnh .exe + home),
encrypt bằng XOR + base64 (obfuscation, đủ chống user phổ thông).

License vĩnh viễn (unlimited): tắt mặc định qua UNLIMITED_LICENSE_ENABLED.
Khi bật, TrialManager tìm file license.key (cạnh .exe hoặc ~/.dpc_license)
chứa token HMAC-SHA256(machine_id) — sinh token bằng generate_license_token().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

TRIAL_DAYS = 7
_SECRET = b"dpc_2026_trial_x9k2m7v4q1"
_LICENSE_SECRET = b"dpc_2026_license_x7f3k9p2m5"

# Bỏ qua check khi dev (DPC_DEV=1).
_DEV_OVERRIDE = os.environ.get("DPC_DEV") == "1"

# Cờ bật/tắt tính năng license vĩnh viễn — đang TẮT, chưa dùng trong production.
UNLIMITED_LICENSE_ENABLED = False


@dataclass
class TrialStatus:
    is_valid: bool
    days_remaining: int
    is_first_run: bool
    message: str


class TrialManager:
    def __init__(self) -> None:
        self._machine_id = self._compute_machine_id()
        self._locations = self._storage_paths()

    def check(self) -> TrialStatus:
        if _DEV_OVERRIDE:
            return TrialStatus(True, TRIAL_DAYS, False, "Dev mode — bỏ qua trial.")

        if UNLIMITED_LICENSE_ENABLED and self._has_valid_license():
            return TrialStatus(
                is_valid=True,
                days_remaining=-1,
                is_first_run=False,
                message="Bản quyền vĩnh viễn — đã kích hoạt.",
            )

        data = self._read_trial()

        if data is None:
            data = {
                "first_run": datetime.now().isoformat(),
                "machine_id": self._machine_id,
            }
            self._write_trial(data)
            return TrialStatus(
                is_valid=True,
                days_remaining=TRIAL_DAYS,
                is_first_run=True,
                message=f"Bản dùng thử 7 ngày — còn {TRIAL_DAYS} ngày.",
            )

        if data.get("machine_id") != self._machine_id:
            return TrialStatus(
                is_valid=False,
                days_remaining=0,
                is_first_run=False,
                message="License không hợp lệ cho máy này.",
            )

        first_run = datetime.fromisoformat(data["first_run"])
        expiry = first_run + timedelta(days=TRIAL_DAYS)
        now = datetime.now()

        if now >= expiry:
            return TrialStatus(
                is_valid=False,
                days_remaining=0,
                is_first_run=False,
                message="Hết hạn dùng thử. Liên hệ Zalo: 0388279175 để mua license.",
            )

        days_remaining = max(0, (expiry - now).days)
        return TrialStatus(
            is_valid=True,
            days_remaining=days_remaining,
            is_first_run=False,
            message=f"Bản dùng thử — còn {days_remaining} ngày.",
        )

    def _has_valid_license(self) -> bool:
        expected = self.generate_license_token(self._machine_id)
        for loc in self._license_paths():
            try:
                if not loc.exists():
                    continue
                if hmac.compare_digest(loc.read_text().strip(), expected):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _license_paths() -> list[Path]:
        from utils.config_loader import app_base_dir

        return [
            app_base_dir() / "license.key",
            Path.home() / ".dpc_license",
        ]

    @staticmethod
    def generate_license_token(machine_id: str) -> str:
        """Sinh token license vĩnh viễn cho một machine_id cụ thể (khóa theo máy)."""
        return hmac.new(_LICENSE_SECRET, machine_id.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _compute_machine_id() -> str:
        mac = uuid.getnode()
        hostname = socket.gethostname()
        return hashlib.sha256(f"{mac}:{hostname}".encode()).hexdigest()[:32]

    @staticmethod
    def _storage_paths() -> list[Path]:
        from utils.config_loader import app_base_dir

        return [
            app_base_dir() / "trial.dat",
            Path.home() / ".dpc_trial",
        ]

    def _read_trial(self) -> dict | None:
        earliest: tuple[datetime, dict] | None = None

        for loc in self._locations:
            try:
                if not loc.exists():
                    continue
                data = self._decrypt(loc.read_bytes())
                if not data or data.get("machine_id") != self._machine_id:
                    continue
                first_run = datetime.fromisoformat(data["first_run"])
                if earliest is None or first_run < earliest[0]:
                    earliest = (first_run, data)
            except Exception:
                continue

        return earliest[1] if earliest else None

    def _write_trial(self, data: dict) -> None:
        raw = self._encrypt(data)
        for loc in self._locations:
            try:
                loc.parent.mkdir(parents=True, exist_ok=True)
                loc.write_bytes(raw)
            except Exception:
                pass

    @staticmethod
    def _encrypt(data: dict) -> bytes:
        text = json.dumps(data, sort_keys=True)
        raw = text.encode()
        xored = bytes(b ^ _SECRET[i % len(_SECRET)] for i, b in enumerate(raw))
        return base64.b64encode(xored)

    @staticmethod
    def _decrypt(raw: bytes) -> dict | None:
        try:
            xored = base64.b64decode(raw)
            text = bytes(
                b ^ _SECRET[i % len(_SECRET)] for i, b in enumerate(xored)
            ).decode()
            return json.loads(text)
        except Exception:
            return None
