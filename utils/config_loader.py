"""Đọc config/accounts.yaml → list[SiteConfig], merge với block `defaults`.

Ví von: `defaults` là "nội quy chung của toà nhà", mỗi site là "một căn hộ"
có thể ghi đè vài quy tắc riêng. Loader trộn hai lớp này lại.
"""

from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from .models import (
    AuthConfig,
    CacheConfig,
    Credentials,
    FilterConfig,
    RateLimitConfig,
    SiteConfig,
    WatchlistConfig,
)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Trộn `override` lên `base` (đệ quy cho dict lồng nhau)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def app_base_dir() -> Path:
    """Thư mục gốc app: cạnh file .exe khi đóng gói, ngược lại là gốc project.

    PyInstaller --onefile giải nén vào temp (_MEIPASS) nên không dùng __file__
    để tìm config; ta muốn đọc config/accounts.yaml *cạnh file .exe* để user
    sửa được mà không phải build lại.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    return app_base_dir() / "config" / "accounts.yaml"


def load_sites(config_path: str | Path | None = None) -> dict[str, SiteConfig]:
    """Trả về dict {site_id: SiteConfig}. Raise nếu file không tồn tại."""
    path = Path(config_path) if config_path else default_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy config: {path}\n"
            "Hãy copy config/accounts.example.yaml -> config/accounts.yaml và điền tài khoản."
        )

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    defaults: dict[str, Any] = raw.get("defaults", {}) or {}
    sites_raw: dict[str, Any] = raw.get("sites", {}) or {}

    sites: dict[str, SiteConfig] = {}
    for site_id, site_data in sites_raw.items():
        merged = _deep_merge(defaults, site_data or {})
        sites[site_id] = _build_site(site_id, merged)
    return sites


def load_filters(config_path: str | Path | None = None) -> FilterConfig:
    """Đọc block `filters:` toàn cục từ accounts.yaml (thiếu file/block = không lọc)."""
    path = Path(config_path) if config_path else default_config_path()
    if not path.exists():
        return FilterConfig()
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return FilterConfig(**(raw.get("filters") or {}))


def load_watchlist_config(config_path: str | Path | None = None) -> WatchlistConfig:
    """Đọc block `watchlist:` toàn cục từ accounts.yaml (thiếu = mặc định)."""
    path = Path(config_path) if config_path else default_config_path()
    if not path.exists():
        return WatchlistConfig()
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return WatchlistConfig(**(raw.get("watchlist") or {}))


def update_credentials(
    site_id: str,
    username: str,
    password: str,
    config_path: str | Path | None = None,
) -> None:
    """Ghi username/password cho 1 site vào accounts.yaml — surgical, giữ comment.

    Ví von: như dùng bút xóa sửa đúng một dòng trong sổ tay, không chép lại cả sổ.
    Chỉ thay giá trị 2 dòng username/password trong block `credentials:` của site;
    nếu block/field chưa có thì chèn thêm với đúng thụt lề. Toàn bộ comment + format
    phần còn lại giữ nguyên. Ghi atomic (file .tmp rồi os.replace) để không hỏng
    file nếu ghi giữa chừng.
    """
    path = Path(config_path) if config_path else default_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy config: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    site_idx, site_end, site_indent = _locate_site_block(lines, site_id)
    new_lines = _apply_credentials(
        lines, site_idx, site_end, site_indent, username, password
    )
    content = "\n".join(new_lines) + "\n"

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_skippable(line: str) -> bool:
    """Dòng trống hoặc comment — bỏ qua khi dò cấu trúc."""
    return not line.strip() or line.lstrip().startswith("#")


def _child_indent(lines: list[str], start: int, end: int, base_indent: int) -> int:
    """Thụt lề của con trực tiếp trong (start, end); mặc định base + 2 nếu chưa có con."""
    for i in range(start + 1, end):
        if _is_skippable(lines[i]):
            continue
        ind = _line_indent(lines[i])
        if ind > base_indent:
            return ind
    return base_indent + 2


def _locate_site_block(lines: list[str], site_id: str) -> tuple[int, int, int]:
    """Trả (index dòng `<site_id>:`, index kết thúc block, thụt lề site).

    Chỉ nhận header ở đúng mức thụt của con trực tiếp dưới `sites:` (tránh khớp nhầm
    một key lồng sâu trùng tên). Raise nếu thiếu `sites:` hoặc không thấy site.
    """
    sites_idx = None
    for i, line in enumerate(lines):
        if _line_indent(line) == 0 and line.strip() == "sites:":
            sites_idx = i
            break
    if sites_idx is None:
        raise ValueError("accounts.yaml không có block 'sites:'")

    child_indent: int | None = None
    site_idx = None
    for i in range(sites_idx + 1, len(lines)):
        line = lines[i]
        if _is_skippable(line):
            continue
        indent = _line_indent(line)
        if indent == 0:
            break  # đã ra khỏi block sites
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue
        key = line.strip().split(":", 1)[0].strip()
        if key == site_id:
            site_idx = i
            break
    if site_idx is None or child_indent is None:
        raise ValueError(f"Không tìm thấy site '{site_id}' trong accounts.yaml")

    site_end = len(lines)
    for i in range(site_idx + 1, len(lines)):
        line = lines[i]
        if _is_skippable(line):
            continue
        if _line_indent(line) <= child_indent:
            site_end = i
            break
    return site_idx, site_end, child_indent


def _dq(value: str) -> str:
    """Bọc scalar trong ngoặc kép YAML, escape backslash và dấu ngoặc kép."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _apply_credentials(
    lines: list[str],
    site_idx: int,
    site_end: int,
    site_indent: int,
    username: str,
    password: str,
) -> list[str]:
    """Thay/chèn username+password trong block credentials của site (in-place copy)."""
    lines = list(lines)

    cred_idx = None
    for i in range(site_idx + 1, site_end):
        if lines[i].strip() == "credentials:":
            cred_idx = i
            break

    # Site chưa có block credentials → chèn nguyên block ngay sau header site.
    if cred_idx is None:
        field_indent = _child_indent(lines, site_idx, site_end, site_indent) + 2
        block = [
            f"{' ' * (site_indent + 2)}credentials:",
            f"{' ' * field_indent}username: {_dq(username)}",
            f"{' ' * field_indent}password: {_dq(password)}",
        ]
        lines[site_idx + 1 : site_idx + 1] = block
        return lines

    cred_indent = _line_indent(lines[cred_idx])
    cred_end = site_end
    for i in range(cred_idx + 1, site_end):
        if _is_skippable(lines[i]):
            continue
        if _line_indent(lines[i]) <= cred_indent:
            cred_end = i
            break
    field_indent = _child_indent(lines, cred_idx, cred_end, cred_indent)

    found = {"username": False, "password": False}
    for i in range(cred_idx + 1, cred_end):
        for field, value in (("username", username), ("password", password)):
            m = re.match(rf"^(\s*){field}\s*:", lines[i])
            if m:
                lines[i] = f"{m.group(1)}{field}: {_dq(value)}"
                found[field] = True

    to_insert = [
        f"{' ' * field_indent}{field}: {_dq(value)}"
        for field, value in (("username", username), ("password", password))
        if not found[field]
    ]
    if to_insert:
        lines[cred_idx + 1 : cred_idx + 1] = to_insert
    return lines


def _build_site(site_id: str, data: dict[str, Any]) -> SiteConfig:
    """Dựng SiteConfig từ dict đã merge (pydantic validate từng block)."""
    return SiteConfig(
        id=site_id,
        name=data.get("name", site_id),
        enabled=bool(data.get("enabled", True)),
        base_url=data.get("base_url", ""),
        login_url=data.get("login_url", ""),
        search_url=data.get("search_url", ""),
        credentials=Credentials(**(data.get("credentials") or {})),
        auth=AuthConfig(**(data.get("auth") or {})),
        cache=CacheConfig(**(data.get("cache") or {})),
        rate_limit=RateLimitConfig(**(data.get("rate_limit") or {})),
        user_agent=data.get("user_agent", SiteConfig.model_fields["user_agent"].default),
    )
