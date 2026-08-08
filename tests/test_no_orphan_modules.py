"""Đếm mồ côi ở mức MODULE — bổ sung cho `test_no_orphans.py` (mức hàm chấm).

`test_no_orphans.py` cưỡng chế đúng sáu hàm chấm của tầng gate. Nó không thấy
được lớp lỗi lớn hơn: cả một module có test riêng, có docstring dài, có mọi dấu
hiệu của mã đang phục vụ — mà không dòng sản phẩm nào import. Đã đếm được 6
module ở trạng thái đó, trong đó `core/grid/score.py` là bản SAO của
`core/grid/axis_score.py` (cùng `SearchParams`, cùng `load_search_params`); nó
đã bị xoá, và tệp này tồn tại để cái tiếp theo không lặng lẽ mọc lên.

Cách tệp này KHÔNG hoạt động: nó không đòi mọi module phải được nối. Một module
mồ côi có thể là nợ đã biết và có lý do. Nó đòi tập mồ côi phải ĐÚNG BẰNG tập đã
ghim bên dưới — thêm một cái mới là ĐỎ, nối được một cái cũ cũng là ĐỎ (và cách
sửa là xoá nó khỏi bảng, kèm cập nhật lý do).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "src" / "backend" / "app"

if str(REPO_ROOT / "src" / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src" / "backend"))

#: Module chưa có nơi gọi sản phẩm, kèm LÝ DO. Lý do bắt buộc khác rỗng: một
#: bảng miễn trừ không giải thích được là một bảng sẽ dài mãi.
KNOWN_ORPHANS: dict[str, str] = {
    "core/exec/calibrate.py": (
        "Hồi sinh ~10% ô đã bị gọi là high/N-A để đo false_high_rate. Cần một "
        "lượt mutmut thật cho mỗi ô hồi sinh; luồng analyze hiện replay artifact "
        "precomputed nên chưa có chỗ cắm."
    ),
    "core/grid/escalate.py": (
        "Escalation t=2 → t=3 cho hot zone. Nối vào sẽ ĐỔI TẬP Ô của mọi repo "
        "mẫu ⇒ đổi con số trong prompt ⇒ đổi cassette_key ⇒ toàn bộ cassette "
        "đang commit trượt. Phải nối và thu lại cassette trong CÙNG một lần."
    ),
    "core/stats/cluster.py": (
        "Hiệu chỉnh cụm cho khoảng tin cậy. Cùng lý do chặn với escalate: nó đổi "
        "biên của interval, mà interval nằm trong prompt."
    ),
    "core/stats/judge.py": (
        "Hiệu chỉnh LLM-judge. Luồng analyze hiện không có bước judge nào để "
        "hiệu chỉnh; nó phục vụ tầng eval, chưa phải tầng chạy."
    ),
    "core/exec/coverage_reader.py": "",  # đã nối — xem khẳng định ở cuối tệp
}

#: Module không cần nơi gọi: entrypoint, gói rỗng, tệp lắp ráp.
NOT_APPLICABLE: frozenset[str] = frozenset(
    {
        "main.py",  # uvicorn nạp bằng chuỗi "app.main:app"
        "settings.py",  # nạp qua `from app.settings import settings` (không phải Call)
    }
)


def _module_key(path: Path) -> str:
    return str(path.relative_to(APP_ROOT))


def _imported_modules() -> set[str]:
    """Tập module được import bởi một module SẢN PHẨM khác (không tính chính nó)."""
    imported: set[str] = set()
    for source in APP_ROOT.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
                # `from app.api.routes import auth, chat` — tên module con nằm ở
                # `names`, không ở `node.module`. Bỏ nhánh này thì 10 module bị
                # đếm nhầm là mồ côi trong khi `main.py` nạp chúng đúng kiểu đó,
                # và một bộ đếm mồ côi báo dương tính giả sẽ bị tắt sau hai lần.
                names.extend(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for dotted in names:
                if not dotted.startswith("app."):
                    continue
                candidate = APP_ROOT / (dotted[len("app.") :].replace(".", "/") + ".py")
                if candidate.is_file() and candidate != source:
                    imported.add(_module_key(candidate))
    return imported


def _product_modules() -> set[str]:
    return {
        _module_key(p)
        for p in APP_ROOT.rglob("*.py")
        if p.name != "__init__.py" and _module_key(p) not in NOT_APPLICABLE
    }


def test_the_scan_actually_scans_something() -> None:
    """Đối chứng dương. Một tập rỗng đọc y hệt nhau ở 'sạch' và ở 'quét trượt'."""
    modules = _product_modules()
    assert len(modules) > 40, f"chỉ quét được {len(modules)} module — bộ quét hỏng"
    assert _imported_modules(), "không thấy import nào — bộ quét hỏng"


def test_orphan_set_matches_the_pinned_table() -> None:
    orphans = _product_modules() - _imported_modules()
    pinned = {name for name, reason in KNOWN_ORPHANS.items() if reason}

    new = sorted(orphans - pinned)
    assert not new, (
        f"module mới không có nơi gọi sản phẩm: {new}. Nối nó vào, xoá nó, hoặc "
        f"thêm vào KNOWN_ORPHANS KÈM LÝ DO."
    )

    fixed = sorted(pinned - orphans)
    assert not fixed, (
        f"module trong KNOWN_ORPHANS nay đã được nối: {fixed}. Xoá khỏi bảng — "
        f"một bảng nợ không được giữ lại những món đã trả."
    )


def test_every_pinned_orphan_carries_a_reason() -> None:
    """Miễn trừ không lý do là miễn trừ vĩnh viễn."""
    blank = [name for name, reason in KNOWN_ORPHANS.items() if not reason.strip()]
    assert blank == ["core/exec/coverage_reader.py"], (
        f"mục miễn trừ không có lý do: {blank}"
    )


def test_coverage_reader_is_wired_into_the_pipeline() -> None:
    """Khẳng định dương cho món nợ vừa trả.

    `coverage_reader` từng mồ côi trong khi `pipeline.run_target_suite` tự parse
    `.coverage` và nuốt mọi lỗi bằng `except Exception: lines = 0`. Cửa
    `_finish()` của nó — "report rỗng thì NỔ" — nay nằm trên đường chạy.
    """
    source = (APP_ROOT / "orchestrator" / "pipeline.py").read_text(encoding="utf-8")
    assert "read_coverage_data" in source
