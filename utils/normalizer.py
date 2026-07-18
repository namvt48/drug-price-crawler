"""Chuẩn hoá tên thuốc: gom biến thể liên nguồn về canonical_name.

Ví von: cùng một thuốc nhưng mỗi nhà thuốc đặt tên khác nhau (khác bao bì,
khác dạng bào chế) — module này là "người phiên dịch" tìm điểm chung để
gom chúng về một cái tên duy nhất, vẫn phân biệt được các sản phẩm khác nhau.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from utils.config_loader import app_base_dir

# Bỏ noise đóng gói: "(H/100V)", "h/5v", "5 vi x 10 vien", "hop 1 lo 30 vien",
# "lo 100ml", "20v", "100mg" v.v. — Stage 1 dùng trước khi tách brand/form/maker.
_NOISE_RE = re.compile(
    r"""
    \(.*?\)
    | h\s*/\s*\d+\s*v
    | \d+\s*(vi|v)\s*x\s*\d+\s*(vien|v)
    | hop\s+\d+.*?vien
    | lo\s+\d+\s*ml
    | \b\d+\s*(ml|mg|g|vien|v|vi|lo|hop|goi|tuyp)\b
    """,
    re.VERBOSE,
)

# Các dạng bào chế — dùng để tách sản phẩm khác dạng nhau (Forte vs siro vs ...).
FORM_KW: list[str] = [
    "bao duong", "bao phim", "nen", "nang", "siro", "forte", "premium",
    "gel", "kem", "dung dich", "vien sui", "com", "bot",
]

# Từ thuộc FORM_KW (flatten multiword) — kiểm tra token cuối có phải dạng bào chế.
_FORM_WORDS: set[str] = {w for kw in FORM_KW for w in kw.split()}


def strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt: đ→d, Đ→D, rồi NFD + bỏ ký tự Mn."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def canonical_key(name: str) -> str:
    """Trả về khoá canonical 'brand|form|maker' cho một tên thuốc."""
    s = strip_accents(name).lower()
    s = _NOISE_RE.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = s.split() if s else []
    if not tokens:
        return "||"

    brand = tokens[0]
    maker = "" if tokens[-1] in _FORM_WORDS else tokens[-1]

    # Stage 1: tìm FORM_KW trong dòng token (match dài nhất trước, xoá khi tìm thấy).
    joined = " " + " ".join(tokens) + " "
    found: list[str] = []
    for kw in sorted(FORM_KW, key=len, reverse=True):
        if f" {kw} " in joined:
            found.append(kw)
            joined = joined.replace(f" {kw} ", " ")

    form = " ".join(sorted(found))
    return f"{brand}|{form}|{maker}"


def display_name(key: str) -> str:
    """Dựng tên hiển thị Title-case từ khoá canonical 'brand|form|maker'."""
    brand, form, maker = key.split("|", 2)
    parts = [brand.title()]
    if form:
        parts.append(form.title())
    if maker:
        parts.append(maker.title())
    return " ".join(parts).strip()


def load_aliases(path: str | Path | None = None) -> dict[str, str]:
    """Đọc config/name_aliases.yaml → {variant_lower: canonical_display}."""
    p = Path(path) if path else (app_base_dir() / "config" / "name_aliases.yaml")
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    flat: dict[str, str] = {}
    for canonical, variants in raw.items():
        if not isinstance(variants, list):
            continue
        for v in variants:
            if isinstance(v, str):
                flat[v.strip().lower()] = canonical
    return flat


def group_names(names: list[str], threshold: int = 60) -> dict[str, list[str]]:
    """Gom các tên biến thể về nhóm canonical (Stage 1 + Stage 2 fuzzy merge)."""
    # Stage 1: khoá canonical cho từng tên.
    key_to_names: dict[str, list[str]] = {}
    for n in names:
        key_to_names.setdefault(canonical_key(n), []).append(n)

    unique_keys = list(key_to_names.keys())
    if len(unique_keys) <= 1:
        return {display_name(k): key_to_names[k] for k in unique_keys}

    # Stage 2: fuzzy-merge các khoá cùng (brand, maker) và form gần nhau (union-find).
    parent: dict[str, str] = {k: k for k in unique_keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_brand_maker: dict[tuple[str, str], list[str]] = {}
    for k in unique_keys:
        brand, _, maker = k.split("|", 2)
        by_brand_maker.setdefault((brand, maker), []).append(k)

    for bm_keys in by_brand_maker.values():
        for i in range(len(bm_keys)):
            for j in range(i + 1, len(bm_keys)):
                k1, k2 = bm_keys[i], bm_keys[j]
                _, f1, _ = k1.split("|", 2)
                _, f2, _ = k2.split("|", 2)
                if not f1 or not f2:
                    continue
                if fuzz.token_sort_ratio(f1, f2) >= threshold:
                    union(k1, k2)

    # Đại diện = khoá đầu tiên (theo thứ tự xuất hiện) trong mỗi nhóm.
    root_to_first: dict[str, str] = {}
    for k in unique_keys:
        root = find(k)
        if root not in root_to_first:
            root_to_first[root] = k

    merged: dict[str, list[str]] = {}
    for k in unique_keys:
        rep = root_to_first[find(k)]
        merged.setdefault(rep, [])
        merged[rep].extend(key_to_names[k])

    return {display_name(rep): names_list for rep, names_list in merged.items()}


def canonical_for(name: str, aliases: dict[str, str] | None = None) -> str:
    """Tên canonical cho một tên đơn lẻ: alias thắng, còn lại dùng display_name."""
    if aliases:
        key = name.strip().lower()
        if key in aliases:
            return aliases[key]
    return display_name(canonical_key(name))
