"""Nối bộ kiểm thử của repo đích vào từng ô của lưới.

Đây là chỗ câu hỏi "độ phủ 98%" và câu hỏi "còn góc rủi ro nào chưa ai nhìn"
tách khỏi nhau. `coverage.py` trả lời câu thứ nhất và trả lời rất tốt. Câu thứ
hai không có thư viện nào trả lời, vì nó phụ thuộc vào việc CÁI GÌ đáng gọi là
một góc rủi ro — và đó là quyết định của người, không của công cụ.

Cách suy ra, nói thẳng để không ai đọc nhầm mức chắc chắn:

  Một ô `(trục_a=x, trục_b=y)` được coi là CÓ NGƯỜI CANH khi tồn tại ít nhất
  một hàm test nhắc tới cả hai giá trị đó. Đây là suy luận theo **cú pháp**,
  không phải theo vết chạy: một test nhắc `VIP` và `INTERNATIONAL` mà không
  thật sự gọi tổ hợp đó vẫn được tính. Nói cách khác phép đo này **rộng rãi
  hơn sự thật** — nó ước lượng TRÊN, nên con số phủ thật chỉ có thể thấp hơn
  con số nó báo, không bao giờ cao hơn.

Ghi cái giới hạn đó ngay ở đây, thay vì trong một tệp tài liệu mà không ai mở,
là có chủ đích: con số này đi thẳng lên UI.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TestFn:
    """Một hàm test và những gì đọc được từ nó bằng mắt thường của trình phân tích."""

    name: str
    file: str
    line: int
    names_used: set[str] = field(default_factory=set)
    assert_count: int = 0


def scan_tests(root: Path) -> list[TestFn]:
    """Đọc mọi hàm `test_*` trong repo đích.

    Đếm assert bằng AST chứ không bằng `grep "assert"`: chuỗi tài liệu và tên
    biến chứa chữ assert sẽ làm con số phồng lên, và một mẫu số phồng lên là
    đúng thứ cả sản phẩm này tồn tại để chống.
    """
    out: list[TestFn] = []
    for py in sorted(root.rglob("test_*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = str(py.relative_to(root))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            fn = TestFn(name=node.name, file=rel, line=node.lineno)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert):
                    fn.assert_count += 1
                elif isinstance(sub, ast.Attribute):
                    fn.names_used.add(sub.attr.lower())
                elif isinstance(sub, ast.Name):
                    fn.names_used.add(sub.id.lower())
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    fn.names_used.add(sub.value.lower())
            out.append(fn)
    return out


def observations_for_cells(
    tests: list[TestFn],
    *,
    code_path: str,
    cov_suite: set[str],
    suite_exit_code: int,
) -> dict[tuple[str, ...], dict[str, Any]]:
    """Lập bảng tra: bộ giá trị của ô → quan sát.

    Khoá là **tuple giá trị đã sắp**, không phải id ô: cùng một cặp giá trị có
    thể xuất hiện dưới nhiều tên ô khác nhau nếu axis lock đổi, và bảng tra phải
    sống sót qua chuyện đó.
    """
    table: dict[tuple[str, ...], dict[str, Any]] = {}
    for fn in tests:
        vals = sorted(fn.names_used)
        for i, a in enumerate(vals):
            for b in vals[i + 1 :]:
                key = (a, b)
                prev = table.get(key)
                if prev is None or fn.assert_count > prev["assert_count"]:
                    table[key] = {
                        "test_exit_code": suite_exit_code,
                        "assert_count": fn.assert_count,
                        "code_path": code_path,
                        "cov_cell": {code_path} if code_path in cov_suite else set(),
                        "cov_suite": cov_suite,
                        "evidence_id": [f"test:{fn.file}::{fn.name}"],
                    }
    return table


def lookup(
    table: dict[tuple[str, ...], dict[str, Any]], values: tuple[str, ...]
) -> dict[str, Any] | None:
    return table.get(tuple(sorted(v.lower() for v in values)))


# ── mutation: replay artifact precomputed ───────────────────────────────────
#
# Vì sao PRECOMPUTED chứ không chạy mutmut lúc phân tích: giống hệt lý do có
# cassette. mutmut cấy hàng trăm mutant rồi chạy lại toàn bộ bộ kiểm cho từng
# cái — hàng phút mỗi repo, không thể để 1000 sinh viên kích hoạt đồng thời. Nên
# host chạy `scripts/record_mutations.py` MỘT LẦN, commit JSON, và pipeline
# replay nó. KHÔNG có file ⇒ cổng mutation giữ nguyên fail-closed
# (`mutation_missing`) — thiếu bằng chứng là "không có bằng chứng", không phải
# "coi như đã kiểm".
#
# Vì sao neo theo ZONE chứ không rải đều mọi ô: artifact khai đúng MỘT zone mà
# host đã xác nhận bằng lượt mutmut thật rằng hàm phục vụ zone đó bị mutant
# giết. Một verdict "killed" mượn sang zone khác (concurrency không cấy được
# mutation, checkout_core chưa chạy mutmut) chính là con số phủ không neo mà cả
# sản phẩm này tồn tại để chống.

_MUTATION_REQUIRED_KEYS = (
    "target",
    "zone_id",
    "verdict",
    "seed_id",
    "probe_sha256",
    "operator",
    "target_path",
)


def load_mutation_artifact(
    target: str, mutations_dir: Path
) -> dict[str, Any] | None:
    """Đọc artifact mutation precomputed cho một repo mẫu, hoặc None nếu vắng.

    Chỉ chấp nhận artifact đủ mọi khoá bắt buộc và đúng `target`; thiếu một khoá
    ⇒ trả None (không đoán). Hỏng JSON cũng ⇒ None: một artifact không đọc được
    không được phép câm lặng nâng band.
    """
    if not target:
        return None
    path = Path(mutations_dir) / f"{target}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(data.get(k) for k in _MUTATION_REQUIRED_KEYS):
        return None
    if data.get("target") != target:
        return None
    return data


def mutation_counts(artifact: dict[str, Any] | None) -> tuple[int, int] | None:
    """(mutant bị giết, tổng mutant) của artifact — hoặc None nếu không có artifact.

    Đây là MẪU SỐ THỨ BA mà README hứa và trước đây không ai tính:
    `CoverageOut.mutation` được khai trong schema nhưng chưa từng được gán, nên
    CLI in ra hai dòng trong khi tài liệu nói ba.

    Hai dạng artifact:

    * dạng GIÀU — host chạy mutmut trên nhiều mutant rồi khai thẳng `killed` và
      `total`. Dùng nguyên hai con số đó.
    * dạng MỘT-MUTANT (bản đang commit trong `fixtures/mutations/`) — artifact
      neo về đúng một mutant có `operator` + `target_path` cụ thể. Lúc đó
      k/n = 1/1 hoặc 0/1.

    Cỡ mẫu 1 KHÔNG bị làm tròn thành "đã kiểm mutation": `rate()` gắn cờ
    `n-too-small` và Wilson trả về một khoảng gần như toàn dải. Đó chính là bài
    học `M1` của DEBTS.md — bản ghi gốc `{"killed":0,"survived":2,"rate":1.0}`
    đọc như 100% trong khi cỡ mẫu 2 chưa nói được gì. Ở đây con số nhỏ vẫn hiện
    ra, nhưng hiện ra KÈM khoảng của nó.
    """
    if not artifact:
        return None
    killed = artifact.get("killed")
    total = artifact.get("total")
    if isinstance(killed, int) and isinstance(total, int) and total > 0 and killed <= total:
        return killed, total
    return (1 if artifact.get("verdict") == "killed" else 0), 1


def mutation_run_for_cell(
    artifact: dict[str, Any] | None, *, zone_id: str, cell_id: str
) -> dict[str, Any] | None:
    """Dựng dict `mutation_run` cho một ô, hoặc None nếu artifact không phủ ô.

    Chỉ phủ ĐÚNG zone mà artifact khai. Binding mang đủ ba trường
    (`probe_sha256`, `seed_id`, `cell_id`) mà `project_cell._is_bound` đòi; neo
    `cell_id` là chính ô này, không phải một hằng số dùng lại. `seed_id` trùng
    với `calibration_seed_id` mà pipeline đặt cạnh nó — thiếu khớp là fail-closed
    ở `project_cell`, không phải "coi như khớp".
    """
    if not artifact or zone_id != artifact.get("zone_id"):
        return None
    seed = artifact["seed_id"]
    return {
        "verdict": artifact["verdict"],
        "seed_id": seed,
        # round_had_survived + verdict killed ⇒ mutation_round_conflict ở
        # project_cell. Artifact chỉ khai killed khi phạm vi zone KHÔNG có mutant
        # sống, nên cờ này là False ở đường killed — giữ đúng ngữ nghĩa.
        "round_had_survived": bool(artifact.get("round_had_survived", False)),
        # Binding đủ 5 trường mà grid.yaml đòi (probe_sha256, seed_id, cell_id,
        # operator, target_path). `operator` + `target_path` neo về MỘT mutant
        # killed cụ thể (host ghi trong artifact) — `high` nghĩa "mutant NÀY bị
        # giết", không phải verdict trôi nổi. Thiếu một trường ⇒ mutation_unbound.
        "binding": {
            "probe_sha256": artifact["probe_sha256"],
            "seed_id": seed,
            "cell_id": cell_id,
            "operator": artifact.get("operator"),
            "target_path": artifact.get("target_path"),
        },
    }
