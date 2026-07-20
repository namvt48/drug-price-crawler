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

# Bỏ noise ĐÓNG GÓI thuần túy (số lượng, không phải hàm lượng): "(H/100V)",
# "h/5v", "h3*10v" (dấu * thay / cũng gặp), "5 vi x 10 vien", "hop 1 lo 30
# vien", "20v" v.v. KHÔNG còn gồm mg/ml/g nữa — xem `_STRENGTH_RE` bên dưới,
# vì hàm lượng (500mg vs 625mg, hay kích cỡ đóng gói 25g vs 100g) thường
# CHÍNH LÀ thứ phân biệt 2 SKU thật khác nhau (giá khác nhau) chứ không phải
# nhiễu để xoá.
_PACK_NOISE_RE = re.compile(
    r"""
    \(.*?\)
    | h\s*[/*]\s*\d+\s*v
    | \d+\s*(vi|v)\s*x\s*\d+\s*(vien|v)
    | hop\s+\d+.*?vien
    | \b\d+\s*(vien|v|vi|lo|hop|goi|tuyp)\b
    """,
    re.VERBOSE,
)

# Tiền tố ghi chú kiểm định/lô hàng ("KĐ.", "KĐ ") — sau strip_accents đ→d
# thành "kd" — xuất hiện trước hàng nghìn tên SP thuộc mọi hãng khác nhau,
# KHÔNG phải brand thật (giống ngày tháng/hóa đơn, chỉ khác đứng ở ĐẦU tên
# thay vì cuối, nên gây sai brand thay vì sai maker nếu để sót).
_PREFIX_NOTE_RE = re.compile(r"^kd\.?\s*")

# Ngày tháng/hạn dùng kiểu "date 01/26", "date 12.2027", "7/27" — không liên
# quan tới nhận diện sản phẩm, nhưng nếu để sót sẽ bị vơ nhầm làm "maker"
# (token cuối cùng còn lại), gây xé lẻ 1 sản phẩm thành nhiều nhóm khác nhau
# chỉ vì khác ngày hết hạn giữa các lần crawl.
_DATE_RE = re.compile(r"\bdate\s+\d{1,2}[./]\d{2,4}\b|\b\d{1,2}[./]\d{2,4}\b")

# Ghi chú người bán hay chèn thẳng vào tên sản phẩm (không phải 1 phần tên
# thật): "hóa đơn", "hóa đơn nhanh" — nếu để sót cũng bị vơ nhầm làm "maker"
# giống ngày tháng, xé lẻ sản phẩm giống hệt nhau thành nhiều nhóm.
_NOTE_NOISE_RE = re.compile(r"\bhoa don\b(\s+nhanh)?")

# Hàm lượng/kích cỡ thật (mg/mcg/g/gr/ml/IU/%) — GIỮ LẠI trong khoá canonical
# (khác `_PACK_NOISE_RE`), tách riêng thành phần `strength` để Stage 2 KHÔNG
# fuzzy-merge xuyên hàm lượng khác nhau (xem `group_names`). "gr" (viết tắt
# gram hay gặp — "20gr", "8gr") KHÔNG khớp unit "g" vì \b đòi biên từ ngay
# sau "g", mà "r" tiếp liền không phải biên từ — phải khai báo riêng.
_STRENGTH_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mg|mcg|gr|g|ml|iu|%)\b")
_STRENGTH_UNIT_ALIASES = {"gr": "g"}

# Các dạng bào chế — dùng để tách sản phẩm khác dạng nhau (Forte vs siro vs
# ...). Có cả từ tiếng Anh (tablets/tab/cap/inj/suspension) vì vài site viết
# tên gốc tiếng Anh — thiếu thì từ này lọt xuống cuối câu, bị hiểu nhầm thành
# "maker" (vd "Zinnat Tablets 500mg" -> maker="tablets", sai).
FORM_KW: list[str] = [
    "bao duong", "bao phim", "nen", "nang", "siro", "forte", "premium",
    "gel", "kem", "dung dich", "vien sui", "com", "bot",
    "tablets", "tab", "capsules", "cap", "suspension", "syrup", "inj",
    "injection", "cream", "solution",
]

# Từ thuộc FORM_KW (flatten multiword) — kiểm tra token cuối có phải dạng bào chế.
_FORM_WORDS: set[str] = {w for kw in FORM_KW for w in kw.split()}


def strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt: đ→d, Đ→D, rồi NFD + bỏ ký tự Mn."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _extract_brand(tokens: list[str]) -> tuple[str, int]:
    """Token(s) đầu làm brand. Brand viết tắt kiểu "A.T"/"T.W" bị dấu chấm
    tách rời thành từng ký tự đơn ("a","t") — gộp lại thành 1 token thật
    ("at") thay vì chỉ lấy ký tự đầu tiên (vô nghĩa, khiến mọi sản phẩm của
    hãng đó rơi vào chung 1 "brand" 1-ký-tự, dễ gộp nhầm các thuốc khác hẳn
    nhau — xem case 'A.T Ascorbic'/'A.T Desloratadin'/'A.T Zinc' từng bị gộp
    chung trước khi sửa). Trả về (brand, số token đã dùng)."""
    lead = 0
    while lead < len(tokens) and len(tokens[lead]) == 1:
        lead += 1
    if lead == 0:
        return tokens[0], 1
    if lead >= len(tokens):
        return "".join(tokens), len(tokens)
    return "".join(tokens[:lead]), lead


def canonical_key(name: str) -> str:
    """Trả về khoá canonical 'brand|form|strength|maker' cho một tên thuốc.

    `strength` (hàm lượng/kích cỡ: 500mg, 100ml, 25g...) tách riêng khỏi
    `form` — 2 biến thể khác nhau CHỈ khi khác hàm lượng sẽ không bao giờ tự
    gộp (xem `group_names` Stage 2), dù `form` giống nhau.

    `form` = MỌI token còn lại giữa brand và maker (không chỉ từ trong
    FORM_KW như trước) — bắt được cả HOẠT CHẤT thật (vd "ascorbic" khác
    "zinc" khác "desloratadin") chứ không chỉ dạng bào chế. Trước đây chỉ
    lọc theo từ điển FORM_KW nên phần hoạt chất bị bỏ hẳn ra khỏi khoá, khiến
    các thuốc khác hẳn nhau (vd "A.T Ascorbic ... An Thiên" và "A.T Zinc ...
    An Thiên") trùng khoá "brand=at, maker=thien" chỉ khác mỗi form rỗng →
    gộp nhầm. `sorted()` để thứ tự từ trong tên không ảnh hưởng khoá (fuzzy
    Stage 2 vẫn xử lý sai khác nhỏ do đánh máy/thiếu từ).
    """
    s = strip_accents(name).lower()

    # Trích strength TRƯỚC KHI xoá ngoặc/packaging — hàm lượng hay bị ghi
    # trong ngoặc (vd "(Hộp/30 ống x 8ml)", "(l/8gr)") mà `_PACK_NOISE_RE`
    # xoá NGUYÊN CỤM `\(.*?\)`. Xoá trước thì hàm lượng bên trong ngoặc mất
    # theo, khiến biến thể có ngoặc và biến thể không ngoặc của CÙNG 1 sản
    # phẩm rơi vào 2 bucket strength khác nhau ("8ml" vs "") — không bao giờ
    # gộp được dù cùng brand/maker/form. Trích trước thì bắt được dù trong
    # hay ngoài ngoặc.
    strengths = sorted({
        f"{value.replace(',', '.')}{_STRENGTH_UNIT_ALIASES.get(unit, unit)}"
        for value, unit in _STRENGTH_RE.findall(s)
    })
    strength = " ".join(strengths)
    s = _STRENGTH_RE.sub(" ", s)

    s = _PREFIX_NOTE_RE.sub("", s)
    s = _PACK_NOISE_RE.sub("", s)
    s = _DATE_RE.sub("", s)
    s = _NOTE_NOISE_RE.sub("", s)

    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = s.split() if s else []
    if not tokens:
        return f"|||{strength}"

    brand, used = _extract_brand(tokens)
    maker = "" if tokens[-1] in _FORM_WORDS else tokens[-1]
    middle = tokens[used:-1] if maker else tokens[used:]
    form = " ".join(sorted(middle))

    return f"{brand}|{form}|{strength}|{maker}"


def display_name(key: str) -> str:
    """Dựng tên hiển thị từ khoá canonical 'brand|form|strength|maker'."""
    brand, form, strength, maker = key.split("|", 3)
    parts = [brand.title()]
    if strength:
        parts.append(strength)
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

    # Stage 2: fuzzy-merge các khoá cùng (brand, strength, maker) và form gần
    # nhau (union-find). `strength` nằm trong khoá bucket CỨNG (không fuzzy)
    # — khác hàm lượng thì không bao giờ vào chung bucket để so form.
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

    by_bucket: dict[tuple[str, str, str], list[str]] = {}
    for k in unique_keys:
        brand, _, strength, maker = k.split("|", 3)
        by_bucket.setdefault((brand, strength, maker), []).append(k)

    for bucket_keys in by_bucket.values():
        for i in range(len(bucket_keys)):
            for j in range(i + 1, len(bucket_keys)):
                k1, k2 = bucket_keys[i], bucket_keys[j]
                _, f1, _, _ = k1.split("|", 3)
                _, f2, _, _ = k2.split("|", 3)
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
