"""Ghim escalation t=2 → t=3 (`core/grid/escalate.py`).

Module này CHƯA được nối vào luồng analyze — nối vào sẽ đổi tập ô của mọi repo
mẫu, tức đổi con số trong prompt, tức làm trượt toàn bộ cassette đang commit
(xem `KNOWN_ORPHANS` trong `tests/test_no_orphan_modules.py`). Nó vẫn phải có
bộ kiểm: mã chết không được test là mã sẽ hỏng lặng lẽ trong lúc nằm chờ, và
lúc ai đó nối nó vào thì không còn ai nhớ nó đáng ra làm gì.

Ba luật ghim ở đây, cả ba đều là chỗ một bản "tối ưu cho gọn" sẽ phá:

1. Hai bậc là HAI KHOÁ RIÊNG. Bằng nhau ⇒ ConfigError, vì mọi ứng viên
   escalation khi đó đã là ô của lưới nền — bước escalate không thêm gì mà vẫn
   báo là đã chạy.
2. Trục hot đo trên Ô THẬT, không đọc tên trục trong `when` của rule.
3. Tầng 2 lọc lại theo zone match THẬT của chính ô, không thừa hưởng độ nóng
   từ trục.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.contracts.errors import ConfigError  # noqa: E402
from app.core.grid.cells import cell_axes  # noqa: E402
from app.core.grid.escalate import (  # noqa: E402
    escalation_candidates,
    hot_axes,
    load_degrees,
)
from app.core.grid.zones import compile_zones  # noqa: E402

HOT_W = 0.85

#: `pay=card` nóng (w=0.95); mọi thứ còn lại rơi vào catch-all lạnh (w=0.2).
RULES = compile_zones(
    [
        {"id": "hot_money", "when": {"pay": ["card"]}, "w": 0.95},
        {"id": "catch_all", "when": {}, "w": 0.20},
    ],
    blocking_w=0.7,
)

AXES = {
    "pay": ["card", "cod"],
    "tier": ["vip", "std"],
    "zone": ["dom", "intl"],
}


# ── bậc: hai khoá riêng biệt ───────────────────────────────────────────────


def test_the_shipped_config_escalates_strictly_above_the_base_degree() -> None:
    degrees = load_degrees()
    assert degrees.base == 2
    assert degrees.escalation > degrees.base


def _write_grid_config(tmp_path: Path, base: int, escalation: int) -> Path:
    (tmp_path / "grid.yaml").write_text(
        textwrap.dedent(
            f"""
            t_wise_degree: {base}
            escalation_degree: {escalation}
            band_scores:
              - {{band: high, score: 1.0}}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize("escalation", [2, 1], ids=["bằng bậc nền", "thấp hơn bậc nền"])
def test_an_escalation_degree_not_above_the_base_is_refused(
    tmp_path: Path, escalation: int
) -> None:
    """Bậc escalation <= bậc nền ⇒ bước escalate không thêm ô nào mà vẫn "đã chạy"."""
    config_dir = _write_grid_config(tmp_path, base=2, escalation=escalation)
    with pytest.raises(ConfigError) as exc:
        load_degrees(config_dir=config_dir)
    assert exc.value.key == "escalation_degree"


# ── tầng 1: trục hot đo trên ô thật ────────────────────────────────────────


def test_only_axes_with_a_real_hot_cell_survive_the_first_filter() -> None:
    hot = hot_axes(AXES, RULES, hot_w=HOT_W, base_degree=2)
    # `pay` nóng vì có ô thật khớp `pay=card`; `tier` và `zone` đi kèm trong
    # chính những ô đó nên cũng được giữ.
    assert set(hot) == {"pay", "tier", "zone"}


def test_an_axis_named_in_a_rule_but_matched_by_no_cell_is_not_hot() -> None:
    """"Hot chỉ trên giấy": rule kể tên một trục mà không ô nào khớp giá trị."""
    rules = compile_zones(
        [
            {"id": "hot_ghost", "when": {"pay": ["crypto"]}, "w": 0.95},
            {"id": "catch_all", "when": {}, "w": 0.20},
        ],
        blocking_w=0.7,
    )
    assert hot_axes(AXES, rules, hot_w=HOT_W, base_degree=2) == []


def test_the_first_filter_preserves_axis_lock_order() -> None:
    """Thứ tự trục là axis lock — đảo nó là đổi cell_id của mọi ô."""
    assert hot_axes(AXES, RULES, hot_w=HOT_W, base_degree=2) == ["pay", "tier", "zone"]


# ── tầng 2: lọc lại theo zone match thật của chính ô ───────────────────────


def _pay_values(cells) -> set[str]:
    return {dict(zip(names, values))["pay"] for names, values in cells}


def test_every_survivor_matches_a_hot_zone_itself() -> None:
    """Một bộ giá trị cụ thể KHÔNG được sống chỉ vì họ hàng của nó nóng."""
    survivors = escalation_candidates(
        AXES, RULES, hot_w=HOT_W, base_degree=2, escalation_degree=3
    )
    assert survivors
    for names, values in survivors:
        assert dict(zip(names, values))["pay"] == "card", (
            "một ô rơi vào catch-all lạnh vẫn sống sót — nó đang thừa hưởng độ "
            "nóng từ trục thay vì từ zone match của chính nó"
        )


def test_survivors_are_third_degree_cells() -> None:
    survivors = escalation_candidates(
        AXES, RULES, hot_w=HOT_W, base_degree=2, escalation_degree=3
    )
    assert all(len(names) == 3 for names, _ in survivors)


def test_escalation_does_not_dedup_against_resolved_pairs() -> None:
    """KHÔNG bỏ ô chỉ vì cả ba cặp con của nó đã có mặt ở t=2.

    Bỏ chúng là vứt đúng lớp lỗi BẬC-3 THUẦN — lỗi tương tác ba chiều vô hình ở
    mọi lát cắt từng cặp — tức chính thứ escalation sinh ra để bắt.
    """
    survivors = escalation_candidates(
        AXES, RULES, hot_w=HOT_W, base_degree=2, escalation_degree=3
    )
    # pay=card × tier{vip,std} × zone{dom,intl} = 4 ô, dù mọi cặp con đã có ở t=2.
    assert len(survivors) == 4


def test_blocking_but_not_hot_yields_no_candidates() -> None:
    """`hot` là tập con THỰC SỰ của `blocking`.

    Zone w=0.75 vẫn chặn (>= blocking_w 0.7) nhưng không nóng (< hot_w 0.85).
    Escalation chỉ phục vụ zone nóng, nên ở đây danh sách phải rỗng — không phải
    "escalate cả lưới cho chắc", vì escalate cả lưới là quay lại đúng ~11.400 ô
    mà hai tầng lọc sinh ra để tránh.
    """
    warm = compile_zones(
        [
            {"id": "warm", "when": {"pay": ["card"]}, "w": 0.75},
            {"id": "catch_all", "when": {}, "w": 0.20},
        ],
        blocking_w=0.7,
    )
    assert (
        escalation_candidates(
            AXES, warm, hot_w=HOT_W, base_degree=2, escalation_degree=3
        )
        == []
    )


def test_excluded_cells_never_become_candidates() -> None:
    """Ô bất khả thi bị loại ở lưới nền không được quay lại qua cửa escalation."""
    survivors = escalation_candidates(
        AXES,
        RULES,
        hot_w=HOT_W,
        base_degree=2,
        escalation_degree=3,
        exclude=lambda cell: cell_axes(cell).get("tier") == "vip",
    )
    assert survivors
    assert all(cell_axes(cell).get("tier") != "vip" for cell in survivors)
