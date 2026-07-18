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
    def test_basic_brand_form_maker(self) -> None:
        key = canonical_key("Boganic siro Traphaco")
        assert key == "boganic|siro|traphaco"

    def test_packaging_noise_removed(self) -> None:
        key1 = canonical_key("Boganic Nén Bao Đường Traphaco (H/100V)")
        key2 = canonical_key("Boganic Nén Bao Đường Traphaco")
        assert key1 == key2

    def test_h_slash_pattern_removed(self) -> None:
        key = canonical_key("Boganic bao duong H/5 vi x 20v Traphaco")
        assert key.startswith("boganic|")
        assert key.endswith("|traphaco")

    def test_ml_removed(self) -> None:
        key = canonical_key("Boganic siro lọ 100ml Traphaco")
        assert key == "boganic|siro|traphaco"

    def test_forte_extracted(self) -> None:
        key = canonical_key("Boganic Forte hộp 5 vỉ x 10 viên nang Traphaco")
        brand, form, maker = key.split("|")
        assert brand == "boganic"
        assert "forte" in form
        assert maker == "traphaco"

    def test_multiple_form_kw(self) -> None:
        key = canonical_key("Boganic Forte Premium Traphaco")
        _, form, _ = key.split("|")
        assert "forte" in form
        assert "premium" in form

    def test_maker_empty_if_last_is_form_word(self) -> None:
        key = canonical_key("Boganic siro")
        brand, form, maker = key.split("|")
        assert maker == ""
        assert form == "siro"

    def test_empty_name(self) -> None:
        assert canonical_key("") == "||"

    def test_single_token(self) -> None:
        key = canonical_key("Paracetamol")
        brand, form, maker = key.split("|")
        assert brand == "paracetamol"
        assert form == ""

    def test_bao_duong_multiword(self) -> None:
        key = canonical_key("Boganic Nén Bao Đường Traphaco (H/100V)")
        _, form, _ = key.split("|")
        assert "bao duong" in form

    def test_form_sorted_alphabetically(self) -> None:
        key = canonical_key("Boganic Premium Forte Traphaco")
        _, form, _ = key.split("|")
        # sorted: forte before premium
        assert form == "forte premium"


class TestDisplayName:
    def test_full_key(self) -> None:
        assert display_name("boganic|siro|traphaco") == "Boganic Siro Traphaco"

    def test_no_form(self) -> None:
        assert display_name("boganic||traphaco") == "Boganic Traphaco"

    def test_no_maker(self) -> None:
        assert display_name("boganic|siro|") == "Boganic Siro"

    def test_no_form_no_maker(self) -> None:
        assert display_name("boganic||") == "Boganic"

    def test_empty_key(self) -> None:
        assert display_name("||") == ""

    def test_multiword_form_titled(self) -> None:
        name = display_name("boganic|bao duong nen|traphaco")
        assert "Bao Duong Nen" in name


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
