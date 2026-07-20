"""Tests cho utils.normalizer — chuẩn hoá tên thuốc."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.normalizer import (
    canonical_for,
    canonical_key,
    display_name,
    group_names,
    load_aliases,
    strip_accents,
)


class TestStripAccents:
    def test_d_to_d(self) -> None:
        assert strip_accents("đ") == "d"

    def test_capital_d(self) -> None:
        assert strip_accents("Đ") == "D"

    def test_removes_diacritics(self) -> None:
        assert strip_accents("Bao Đường") == "Bao Duong"

    def test_lowercased_accents(self) -> None:
        assert strip_accents("á à ả ã ạ") == "a a a a a"

    def test_no_accents_unchanged(self) -> None:
        assert strip_accents("Boganic") == "Boganic"

    def test_empty_string(self) -> None:
        assert strip_accents("") == ""

    def test_mixed(self) -> None:
        assert strip_accents("Viên nén bao đường") == "Vien nen bao duong"


class TestCanonicalKey:
    """Khoá 'brand|form|strength|maker' — 4 phần (trước là 3, không có
    strength). Xem docstring `canonical_key` cho lý do thêm strength + đổi
    form sang "mọi token còn lại" thay vì chỉ khớp từ điển FORM_KW."""

    def test_basic_brand_form_strength_maker(self) -> None:
        key = canonical_key("Boganic siro Traphaco")
        assert key == "boganic|siro||traphaco"

    def test_packaging_noise_removed(self) -> None:
        key1 = canonical_key("Boganic Nén Bao Đường Traphaco (H/100V)")
        key2 = canonical_key("Boganic Nén Bao Đường Traphaco")
        assert key1 == key2

    def test_h_slash_pattern_removed(self) -> None:
        key = canonical_key("Boganic bao duong H/5 vi x 20v Traphaco")
        brand, _form, _strength, maker = key.split("|", 3)
        assert brand == "boganic"
        assert maker == "traphaco"

    def test_strength_kept_not_stripped(self) -> None:
        """Khác bản cũ: hàm lượng/kích cỡ (mg/ml/g) KHÔNG còn bị xoá như rác —
        đây chính là thứ phân biệt 2 SKU khác giá thật (vd Augmentin 500mg
        khác 625mg)."""
        key = canonical_key("Boganic siro lọ 100ml Traphaco")
        _brand, _form, strength, _maker = key.split("|", 3)
        assert strength == "100ml"

    def test_different_strength_different_key(self) -> None:
        key1 = canonical_key("Augmentin 500mg")
        key2 = canonical_key("Augmentin 625mg")
        assert key1 != key2

    def test_strength_decimal_normalized(self) -> None:
        key = canonical_key("Augmentin 62,5mg GSK")
        _brand, _form, strength, _maker = key.split("|", 3)
        assert strength == "62.5mg"

    def test_date_stripped_not_leaked_into_maker(self) -> None:
        """Ngày hết hạn ("date 01/26") từng bị vơ nhầm làm maker, xé lẻ 1 sản
        phẩm giống hệt nhau thành nhiều nhóm chỉ vì khác ngày crawl."""
        key1 = canonical_key("Augmentin 1g GSK date 01/26")
        key2 = canonical_key("Augmentin 1g GSK date 11/25")
        assert key1 == key2
        _brand, _form, _strength, maker = key1.split("|", 3)
        assert maker == "gsk"

    def test_invoice_note_stripped_not_leaked_into_maker(self) -> None:
        """'hóa đơn' (ghi chú người bán) từng bị vơ nhầm làm maker."""
        key1 = canonical_key("Augmentin 1g Gsk hóa đơn")
        key2 = canonical_key("Augmentin 1g Gsk")
        assert key1 == key2

    def test_strength_inside_parens_still_captured(self) -> None:
        """Bug thật: '(Hộp/30 ống x 8ml)' bị `_PACK_NOISE_RE` xoá nguyên cụm
        trong ngoặc — nếu trích strength SAU bước đó thì '8ml' mất theo,
        khiến biến thể có ngoặc và không ngoặc của CÙNG 1 sản phẩm rơi vào
        2 bucket strength khác nhau ('8ml' vs rỗng), không bao giờ gộp được."""
        key1 = canonical_key("A.T Hoạt huyết dưỡng não hộp 30 ống x 8ml An Thiên")
        key2 = canonical_key("AT hoạt huyết dưỡng (Hộp/30 ống x 8ml) - An Thiên")
        _b1, _f1, s1, _m1 = key1.split("|", 3)
        _b2, _f2, s2, _m2 = key2.split("|", 3)
        assert s1 == s2 == "8ml"

    def test_gr_unit_recognized_as_gram(self) -> None:
        """'gr' (viết tắt gram hay gặp) không khớp unit 'g' vì \\b đòi biên
        từ ngay sau, mà 'r' tiếp liền không phải biên — phải khai báo riêng
        và quy về cùng 1 dạng với 'g' (8gr == 8g)."""
        key = canonical_key("Cao xoa bóp bạch hổ hoạt lạc cao bảo linh (l/8gr)")
        _brand, _form, strength, _maker = key.split("|", 3)
        assert strength == "8g"

    def test_abbreviated_brand_glued(self) -> None:
        """Brand viết tắt kiểu "A.T" bị dấu chấm tách rời thành từng ký tự
        đơn ("a","t") — phải gộp lại thành 1 token brand ("at"), không phải
        chỉ lấy chữ cái đầu (vô nghĩa, gây gộp nhầm mọi SP cùng hãng)."""
        key = canonical_key("A.T Ambroxol 30mg An Thiên")
        brand, _form, _strength, _maker = key.split("|", 3)
        assert brand == "at"

    def test_different_active_ingredient_not_collapsed(self) -> None:
        """Trước đây 'form' chỉ khớp từ điển FORM_KW nên hoạt chất thật (vd
        'ascorbic' vs 'zinc') bị bỏ hẳn ra khỏi khoá — 2 thuốc khác nhau hoàn
        toàn cùng hãng/cùng hàm lượng sẽ trùng khoá. Giờ form = mọi token còn
        lại nên bắt được khác biệt này."""
        key1 = canonical_key("A.T Ascorbic siro lọ 60ml An Thiên")
        key2 = canonical_key("A.T ZinC siro lọ 60ml An Thiên")
        assert key1 != key2

    def test_forte_extracted(self) -> None:
        key = canonical_key("Boganic Forte hộp 5 vỉ x 10 viên nang Traphaco")
        brand, form, _strength, maker = key.split("|", 3)
        assert brand == "boganic"
        assert "forte" in form
        assert maker == "traphaco"

    def test_multiple_form_kw(self) -> None:
        key = canonical_key("Boganic Forte Premium Traphaco")
        _, form, _, _ = key.split("|", 3)
        assert "forte" in form
        assert "premium" in form

    def test_maker_empty_if_last_is_form_word(self) -> None:
        key = canonical_key("Boganic siro")
        brand, form, _strength, maker = key.split("|", 3)
        assert maker == ""
        assert form == "siro"

    def test_empty_name(self) -> None:
        assert canonical_key("") == "|||"

    def test_single_token(self) -> None:
        key = canonical_key("Paracetamol")
        brand, form, _strength, _maker = key.split("|", 3)
        assert brand == "paracetamol"
        assert form == ""

    def test_bao_duong_multiword(self) -> None:
        key = canonical_key("Boganic Nén Bao Đường Traphaco (H/100V)")
        _, form, _, _ = key.split("|", 3)
        assert "bao duong" in form

    def test_form_sorted_alphabetically(self) -> None:
        key = canonical_key("Boganic Premium Forte Traphaco")
        _, form, _, _ = key.split("|", 3)
        # sorted: forte before premium
        assert form == "forte premium"

    def test_form_order_independent(self) -> None:
        """Đảo thứ tự từ trong tên (không phải form-word) vẫn ra cùng khoá
        nhờ sort — quan trọng để Stage 1 (exact match) đã tự gộp được, không
        phải đợi Stage 2 fuzzy."""
        key1 = canonical_key("Boganic Forte Premium Traphaco")
        key2 = canonical_key("Boganic Premium Forte Traphaco")
        assert key1 == key2


class TestDisplayName:
    def test_full_key(self) -> None:
        assert display_name("boganic|siro||traphaco") == "Boganic Siro Traphaco"

    def test_no_form(self) -> None:
        assert display_name("boganic|||traphaco") == "Boganic Traphaco"

    def test_no_maker(self) -> None:
        assert display_name("boganic|siro||") == "Boganic Siro"

    def test_no_form_no_maker(self) -> None:
        assert display_name("boganic|||") == "Boganic"

    def test_empty_key(self) -> None:
        assert display_name("|||") == ""

    def test_multiword_form_titled(self) -> None:
        name = display_name("boganic|bao duong nen||traphaco")
        assert "Bao Duong Nen" in name

    def test_strength_included(self) -> None:
        name = display_name("augmentin||1g|gsk")
        assert "1g" in name
        assert "Augmentin" in name
        assert "Gsk" in name


class TestLoadAliases:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.yaml"
        assert load_aliases(p) == {}

    def test_empty_yaml_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("{}", encoding="utf-8")
        assert load_aliases(p) == {}

    def test_comments_only_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "comments.yaml"
        p.write_text("# just comments\n# more comments\n", encoding="utf-8")
        assert load_aliases(p) == {}

    def test_flattens_variants(self, tmp_path: Path) -> None:
        p = tmp_path / "aliases.yaml"
        p.write_text(
            '"Custom Canonical":\n  - "Drug A"\n  - "drug b"\n',
            encoding="utf-8",
        )
        aliases = load_aliases(p)
        assert aliases == {"drug a": "Custom Canonical", "drug b": "Custom Canonical"}

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        p = tmp_path / "aliases.yaml"
        p.write_text('"Std":\n  - "Paracetamol"\n', encoding="utf-8")
        aliases = load_aliases(p)
        assert aliases["paracetamol"] == "Std"

    def test_non_list_value_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "aliases.yaml"
        p.write_text('"Std": "not a list"\n', encoding="utf-8")
        assert load_aliases(p) == {}

    def test_non_string_variant_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "aliases.yaml"
        p.write_text('"Std":\n  - 123\n  - "ok"\n', encoding="utf-8")
        aliases = load_aliases(p)
        assert aliases == {"ok": "Std"}


class TestCanonicalFor:
    def test_alias_override(self) -> None:
        aliases = {"paracetamol": "Paracetamol Standard"}
        assert canonical_for("Paracetamol", aliases) == "Paracetamol Standard"

    def test_alias_case_insensitive(self) -> None:
        aliases = {"paracetamol": "Std"}
        assert canonical_for("PARACETAMOL", aliases) == "Std"

    def test_alias_not_matching_falls_back(self) -> None:
        aliases = {"other": "Other Std"}
        result = canonical_for("Boganic siro Traphaco", aliases)
        assert "Boganic" in result
        assert "Siro" in result

    def test_no_aliases(self) -> None:
        result = canonical_for("Boganic siro Traphaco")
        assert result == "Boganic Siro Traphaco"

    def test_alias_wins_over_auto(self) -> None:
        aliases = {"boganic siro traphaco": "Forced Name"}
        assert canonical_for("Boganic siro Traphaco", aliases) == "Forced Name"


class TestGroupNames:
    VALIDATED_NAMES = [
        "Boganic Forte hộp 5 vỉ x 10 viên nang Traphaco",
        "Boganic hộp 5 vỉ x 20 viên nén bao đường Traphaco",
        "Boganic hộp 5 vỉ x 20 viên bao phim Traphaco",
        "Boganic siro lọ 100ml Traphaco",
        "Boganic Premium hộp 1 lọ 30 viên nang Traphaco",
        "Boganic Nén Bao Đường Traphaco (H/100V)",
        "Boganic bao duong H/5 vi x 20v Traphaco",
    ]

    def test_bao_duong_variants_same_group(self) -> None:
        groups = group_names(self.VALIDATED_NAMES)
        rev = {v: k for k, vs in groups.items() for v in vs}
        # Indices 1, 5, 6 — bao duong variants — same group.
        assert rev[self.VALIDATED_NAMES[1]] == rev[self.VALIDATED_NAMES[5]]
        assert rev[self.VALIDATED_NAMES[1]] == rev[self.VALIDATED_NAMES[6]]

    def test_different_products_different_groups(self) -> None:
        groups = group_names(self.VALIDATED_NAMES)
        rev = {v: k for k, vs in groups.items() for v in vs}
        # forte (0), bao phim (2), siro (3), premium (4) — all different.
        for i in [0, 2, 3, 4]:
            for j in [0, 2, 3, 4]:
                if i < j:
                    assert rev[self.VALIDATED_NAMES[i]] != rev[self.VALIDATED_NAMES[j]], \
                        f"indices {i} and {j} should be different"

    def test_bao_duong_separate_from_others(self) -> None:
        groups = group_names(self.VALIDATED_NAMES)
        rev = {v: k for k, vs in groups.items() for v in vs}
        for idx in [0, 2, 3, 4]:
            assert rev[self.VALIDATED_NAMES[1]] != rev[self.VALIDATED_NAMES[idx]]

    def test_at_least_five_groups(self) -> None:
        groups = group_names(self.VALIDATED_NAMES)
        assert len(groups) >= 5

    def test_bao_duong_group_has_three_members(self) -> None:
        groups = group_names(self.VALIDATED_NAMES)
        rev = {v: k for k, vs in groups.items() for v in vs}
        bao_duong_canon = rev[self.VALIDATED_NAMES[1]]
        assert len(groups[bao_duong_canon]) == 3

    def test_empty_list(self) -> None:
        assert group_names([]) == {}

    def test_single_name(self) -> None:
        groups = group_names(["Boganic siro Traphaco"])
        assert len(groups) == 1
        canon = list(groups.keys())[0]
        assert "Boganic" in canon
        assert "Siro" in canon

    def test_siro_vs_forte_different(self) -> None:
        names = ["Boganic siro Traphaco", "Boganic Forte Traphaco"]
        groups = group_names(names)
        assert len(groups) == 2

    def test_functional_check_example(self) -> None:
        names = [
            "Boganic Nén Bao Đường Traphaco (H/100V)",
            "Boganic bao duong H/5 vi x 20v Traphaco",
            "Boganic siro lọ 100ml Traphaco",
        ]
        groups = group_names(names)
        assert len(groups) == 2
        sizes = sorted(len(v) for v in groups.values())
        assert sizes == [1, 2]

    def test_threshold_default(self) -> None:
        names = ["Boganic bao duong Traphaco", "Boganic bao duong nen Traphaco"]
        groups = group_names(names)
        assert len(groups) == 1

    def test_threshold_high_prevents_merge(self) -> None:
        names = ["Boganic bao duong Traphaco", "Boganic bao duong nen Traphaco"]
        groups = group_names(names, threshold=99)
        assert len(groups) == 2

    def test_different_brand_not_merged(self) -> None:
        names = ["DrugA siro MakerX", "DrugB siro MakerX"]
        groups = group_names(names)
        assert len(groups) == 2

    def test_different_maker_not_merged(self) -> None:
        names = ["Boganic siro Traphaco", "Boganic sico MakerY"]
        groups = group_names(names)
        assert len(groups) == 2

    def test_empty_form_not_merged_with_nonempty(self) -> None:
        # Same (brand, maker) but one has empty form → skip fuzzy, no merge.
        names = ["Boganic Traphaco", "Boganic siro Traphaco"]
        groups = group_names(names)
        assert len(groups) == 2

    def test_different_strength_never_merged_even_if_form_matches(self) -> None:
        """Cốt lõi của bản sửa: cùng brand/form/maker nhưng khác hàm lượng
        (giá thật khác nhau) không bao giờ được tự gộp, dù ngưỡng fuzzy thấp."""
        names = ["Augmentin 500mg GSK", "Augmentin 625mg GSK"]
        groups = group_names(names, threshold=1)
        assert len(groups) == 2

    def test_different_active_ingredient_same_brand_maker_not_merged(self) -> None:
        """Case thật đã gây gộp nhầm nghiêm trọng trước khi sửa: nhiều hoạt
        chất khác nhau cùng hãng 'An Thiên', cùng brand viết tắt 'A.T', cùng
        form rỗng/'siro' → từng gộp chung 1 nhóm. Giờ 'form' chứa cả tên hoạt
        chất nên phải tách ra 3 nhóm riêng."""
        names = [
            "A.T Ascorbic siro lọ 60ml An Thiên",
            "A.T Desloratadin Siro lọ 30ml An Thiên",
            "A.T ZinC siro lọ 60ml An Thiên",
        ]
        groups = group_names(names)
        assert len(groups) == 3
