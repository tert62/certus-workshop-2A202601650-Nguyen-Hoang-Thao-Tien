"""Ghim bốn tool mà mô hình được phép gọi, cộng bước đề xuất trục (HITL).

Vì sao bốn thứ này là TOOL chứ không phải một câu trong prompt: cả bốn đều là
phép đếm hoặc phép đọc, và cả bốn đều sai theo kiểu KHÔNG ném exception. Một mẫu
số sai không làm chương trình dừng, nó chỉ làm con số đẹp lên.

Trọng tâm ghim ở đây là các NHÁNH TỪ CHỐI — chỗ mà một implementation "tiện tay"
sẽ trả 0 hoặc trả rỗng: mẫu số 0 không được thành `0%`, file vắng không được
thành `chưa phủ`, và một đề xuất trống không được đọc thành "bỏ hết trục".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agent.axis_proposal import format_candidates, propose_axes  # noqa: E402
from app.agent.tools.coverage_tools import read_coverage  # noqa: E402
from app.agent.tools.grid_tools import count_grid_cells, read_grid  # noqa: E402
from app.agent.tools.registry import build_default_registry  # noqa: E402
from app.agent.tools.stats_tools import wilson_interval  # noqa: E402

COBERTURA = """<?xml version="1.0" ?>
<coverage>
  <packages><package><classes>
    <class filename="pkg/a.py">
      <lines><line number="1" hits="1"/><line number="2" hits="0"/></lines>
    </class>
    <class filename="pkg/b.py">
      <lines><line number="1" hits="3"/></lines>
    </class>
  </classes></package></packages>
</coverage>
"""


# ── stats_tools ────────────────────────────────────────────────────────────


def test_wilson_center_is_not_p_hat() -> None:
    """`center ≠ p̂` — đúng chỗ trực giác của cả người lẫn model vỡ.

    10/10 cho center ~0.86, không phải 1.00. Một con số sai ở đây trông hoàn
    toàn hợp lý, nên nó phải là tool chứ không phải một câu dặn trong prompt.
    """
    out = wilson_interval(10, 10)
    assert out["p_hat"] == 1.0
    assert out["center"] < 0.9
    assert out["upper"] == pytest.approx(1.0, abs=1e-9)


def test_wilson_names_the_number_used_to_decide() -> None:
    """Số để QUYẾT ĐỊNH là biên dưới; không nêu ra thì câu trả lời tự chọn p̂."""
    assert wilson_interval(3, 3)["decision_number"] == "lower"
    assert wilson_interval(3, 3)["lower"] == pytest.approx(0.4385, abs=5e-4)


def test_wilson_declares_where_the_number_came_from() -> None:
    assert wilson_interval(1, 2)["source"].startswith("core.stats.intervals")


# ── coverage_tools ─────────────────────────────────────────────────────────


def test_read_coverage_counts_hits_per_file(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(COBERTURA, encoding="utf-8")
    out = read_coverage(str(report))

    assert (out["lines_covered"], out["lines_valid"]) == (2, 3)
    assert out["line_rate"] == pytest.approx(2 / 3)
    assert {f["filename"] for f in out["files"]} == {"pkg/a.py", "pkg/b.py"}


def test_read_coverage_returns_no_percent_on_an_empty_denominator(tmp_path: Path) -> None:
    """"0 dòng, phủ 0%" và "chưa đo dòng nào" là hai sự cố khác nhau."""
    report = tmp_path / "coverage.xml"
    report.write_text('<?xml version="1.0" ?><coverage></coverage>', encoding="utf-8")
    out = read_coverage(str(report))

    assert out["lines_valid"] == 0
    assert out["line_rate"] is None, "mẫu số 0 không được biến thành 0.0"


def test_a_missing_report_raises_instead_of_reporting_zero(tmp_path: Path) -> None:
    """Thiếu report là sự cố của bước chạy test, không phải độ phủ 0%."""
    with pytest.raises(FileNotFoundError):
        read_coverage(str(tmp_path / "khong-co.xml"))


# ── grid_tools ─────────────────────────────────────────────────────────────


def test_count_uses_the_closed_form_not_a_hand_count() -> None:
    """(S² − Σaᵢ²)/2 cho t=2: 3×3×2 ⇒ (8² − (9+9+4))/2 = 21."""
    out = count_grid_cells({"a": ["1", "2", "3"], "b": ["x", "y", "z"], "c": ["p", "q"]})
    assert out["count"] == 21
    assert out["axes"] == {"a": 3, "b": 3, "c": 2}
    assert out["t"] == 2


def test_read_grid_histograms_the_bands(tmp_path: Path) -> None:
    grid = tmp_path / "grid.json"
    grid.write_text(
        json.dumps({"cells": [{"band": "high"}, {"band": "unknown"}, {"band": "unknown"}]}),
        encoding="utf-8",
    )
    out = read_grid(str(grid))
    assert out["cells_total"] == 3
    assert out["band_histogram"] == {"high": 1, "unknown": 2}


def test_a_cell_without_a_band_counts_as_unknown(tmp_path: Path) -> None:
    """Ô thiếu band không được rơi khỏi bảng đếm — rơi khỏi bảng là biến mất."""
    grid = tmp_path / "grid.json"
    grid.write_text(json.dumps({"cells": [{}]}), encoding="utf-8")
    assert read_grid(str(grid))["band_histogram"] == {"unknown": 1}


def test_a_missing_grid_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_grid(str(tmp_path / "khong-co.json"))


# ── registry ───────────────────────────────────────────────────────────────


def test_the_registry_exposes_the_four_tools() -> None:
    registry = build_default_registry()
    assert {"wilson_interval", "read_coverage", "count_grid_cells", "read_grid"} <= set(
        registry.names()
    )


def test_every_registered_tool_is_callable_through_the_registry() -> None:
    """Đăng ký một cái TÊN không phải một nơi gọi — `test_no_orphans.py` §1.

    Một sổ đăng ký đầy đủ mà không handler nào chạy được là hình dạng đã có tiền
    lệ; ở đây mỗi tên phải gọi ra được một kết quả thật.
    """
    registry = build_default_registry()
    out = registry.call("wilson_interval", k=1, n=2)
    assert out["lower"] < out["upper"]


def test_the_registry_ships_anthropic_schemas_for_every_tool() -> None:
    registry = build_default_registry()
    schemas = registry.anthropic_schemas()
    assert len(schemas) == len(registry)
    assert all(s.get("name") and s.get("description") for s in schemas)


# ── axis_proposal (HITL) ───────────────────────────────────────────────────


CANDIDATES = [
    {"axis": "payment_method", "members": ["card", "cod"], "source": "enum"},
    {"axis": "cart_state", "members": ["empty", "paid"], "source": "enum"},
]


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeClient:
    """Client giả. `text` None hoặc raise mô phỏng cassette-miss / lỗi mạng."""

    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return FakeResponse(self._text or "")


def test_format_candidates_keeps_the_discovery_order() -> None:
    """Thứ tự trục là axis lock; đảo nó là đổi cell_id của mọi ô."""
    block = format_candidates(CANDIDATES)
    assert block.index("payment_method") < block.index("cart_state")
    assert "2 giá trị: card, cod" in block


@pytest.mark.asyncio
async def test_a_proposal_maps_each_axis_to_keep_and_rationale() -> None:
    client = FakeClient(
        json.dumps(
            {
                "payment_method": {"keep": True, "rationale": "chạm đường tiền"},
                "cart_state": {"keep": False, "rationale": "trùng với trục khác"},
            }
        )
    )
    out = await propose_axes(CANDIDATES, client=client)
    assert out == {
        "payment_method": {"keep": True, "rationale": "chạm đường tiền"},
        "cart_state": {"keep": False, "rationale": "trùng với trục khác"},
    }


@pytest.mark.asyncio
async def test_a_proposal_wrapped_in_a_code_fence_still_parses() -> None:
    """Model mạnh không nhận prefill `{` nên trả JSON bọc trong ```json…```."""
    body = json.dumps({"payment_method": {"keep": True, "rationale": "ok"}})
    out = await propose_axes(CANDIDATES, client=FakeClient(f"```json\n{body}\n```"))
    assert out is not None and out["payment_method"]["keep"] is True


@pytest.mark.asyncio
async def test_an_axis_the_model_invented_is_dropped() -> None:
    """Trục không nằm trong tập ứng viên là trục mô hình bịa — không cho vào lưới."""
    client = FakeClient(json.dumps({"khong_co_that": {"keep": True, "rationale": "x"}}))
    assert await propose_axes(CANDIDATES, client=client) is None


@pytest.mark.parametrize(
    "text",
    ["", "   ", "xin chào, đây không phải JSON", "[1, 2, 3]"],
    ids=["rỗng", "toàn khoảng trắng", "văn xuôi", "không phải object"],
)
@pytest.mark.asyncio
async def test_garbage_becomes_no_proposal_not_a_crash(text: str) -> None:
    """Best-effort có chủ đích: bước HITL vẫn chạy được khi không có đề xuất."""
    assert await propose_axes(CANDIDATES, client=FakeClient(text)) is None


@pytest.mark.asyncio
async def test_a_cassette_miss_becomes_no_proposal() -> None:
    client = FakeClient(error=RuntimeError("cassette miss"))
    assert await propose_axes(CANDIDATES, client=client) is None


@pytest.mark.asyncio
async def test_no_candidates_never_calls_the_model() -> None:
    """Không có trục nào để chấm thì không tiêu một lượt gọi model nào."""
    client = FakeClient(json.dumps({"a": {"keep": True}}))
    assert await propose_axes([], client=client) is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_keep_defaults_to_true_when_the_model_omits_it() -> None:
    """Thiếu `keep` ⇒ GIỮ. Mặc định phải nghiêng về không tự ý bỏ trục của người dùng."""
    client = FakeClient(json.dumps({"payment_method": {"rationale": "không nói rõ"}}))
    out = await propose_axes(CANDIDATES, client=client)
    assert out is not None and out["payment_method"]["keep"] is True


@pytest.mark.asyncio
async def test_an_empty_rationale_becomes_none_not_an_empty_string() -> None:
    client = FakeClient(json.dumps({"payment_method": {"keep": False, "rationale": "  "}}))
    out = await propose_axes(CANDIDATES, client=client)
    assert out is not None and out["payment_method"]["rationale"] is None
