"""Mọi đường dẫn `docs/...` trong mã phải tồn tại, hoặc phải có tên trong bảng nợ.

Đã đếm được 22 trích dẫn tới một cây tài liệu thiết kế không có trong repo này.
Từng cái một thì vô hại; cộng lại chúng dạy người đọc rằng trích dẫn trong repo
này là trang trí, và từ đó không ai kiểm một trích dẫn nào nữa.

Tệp này không đòi phải viết bù tài liệu. Nó đòi tập trích-dẫn-treo phải ĐÚNG BẰNG
tập đã khai trong `docs/design/README.md` — thêm một cái mới là ĐỎ.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Chỉ quét mã và cấu hình. Research note trích dẫn tài liệu gốc bên ngoài repo
#: và đã tự khai điều đó ngay ở đầu tệp.
SCAN_ROOTS = (
    REPO_ROOT / "src" / "backend" / "app",
    REPO_ROOT / "src" / "backend" / "config",
    REPO_ROOT / "src" / "frontend" / "src",
    REPO_ROOT / "evals",
)

SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".yaml", ".md"}

_DOC_REF = re.compile(r"docs/[A-Za-z0-9/._-]+\.md")

#: Tài liệu được trích dẫn nhưng không có trong bản repo lớp học. Mỗi mục phải có
#: một dòng trong bảng của `docs/design/README.md` — test dưới đây kiểm điều đó,
#: nên bảng miễn trừ không thể phình ra mà không ai giải thích.
KNOWN_MISSING: frozenset[str] = frozenset(
    {
        "docs/design/sdd/00-index.md",
        "docs/design/sdd/01-core-stats.md",
        "docs/design/sdd/03-core-exec.md",
        "docs/design/sdd/06-platform.md",
        "docs/design/system-design.md",
        "docs/workshop-plan.md",
    }
)

MAP_FILE = REPO_ROOT / "docs" / "design" / "README.md"


def _referenced_docs() -> dict[str, list[str]]:
    """{đường dẫn tài liệu: [tệp trích dẫn nó]}"""
    found: dict[str, list[str]] = {}
    for root in SCAN_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            for ref in _DOC_REF.findall(path.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(ref, []).append(str(path.relative_to(REPO_ROOT)))
    return found


def test_the_scan_finds_references_at_all() -> None:
    """Đối chứng dương: 0 trích dẫn đọc y hệt 'quét trượt'."""
    assert len(_referenced_docs()) >= 5


def test_no_new_dangling_doc_reference() -> None:
    dangling = {
        ref: sorted(set(sources))
        for ref, sources in _referenced_docs().items()
        if not (REPO_ROOT / ref).is_file()
    }
    new = {ref: src for ref, src in dangling.items() if ref not in KNOWN_MISSING}
    assert not new, (
        f"trích dẫn tài liệu không tồn tại: {new}. Viết tài liệu, sửa đường dẫn, "
        f"hoặc thêm vào KNOWN_MISSING và mô tả nó trong docs/design/README.md."
    )


def test_the_debt_table_does_not_outlive_the_debt() -> None:
    """Tài liệu đã được viết ra mà vẫn nằm trong bảng nợ ⇒ bảng nói dối."""
    stale = sorted(ref for ref in KNOWN_MISSING if (REPO_ROOT / ref).is_file())
    assert not stale, f"đã có tài liệu nhưng vẫn khai là thiếu: {stale}"


def test_every_known_missing_doc_is_explained() -> None:
    """Mỗi món nợ phải có một dòng nói đọc thay ở đâu."""
    assert MAP_FILE.is_file(), "thiếu docs/design/README.md — bảng nợ không có chỗ ở"
    text = MAP_FILE.read_text(encoding="utf-8")
    missing = sorted(ref for ref in KNOWN_MISSING if ref not in text)
    assert not missing, f"chưa mô tả trong docs/design/README.md: {missing}"
