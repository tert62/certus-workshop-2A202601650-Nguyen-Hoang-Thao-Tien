"""Ghim hai thứ trước đây có mặt trong hợp đồng nhưng KHÔNG có mặt trong dữ liệu.

1. `AnalyzeResponse.gates` từng là `[]` hằng số, trong khi luồng đã chấm sàn cho
   từng zone. Hệ quả: panel `GateChain` chỉ có dữ liệu ở chế độ mock — chuỗi cổng,
   thứ cả sản phẩm lấy làm trung tâm, vô hình khi chạy backend thật.
2. Sự kiện SSE `gate` mang dict sàn thô, trong khi `types/sse.ts` khai
   `{ event: 'gate'; data: GateVerdict }`. Hai hình dạng cho một tên sự kiện, và
   frontend phải lọc bỏ cái nó không hiểu.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.api.schemas import AnalyzeResponse, RejectedClaim  # noqa: E402
from app.contracts.types import GateName  # noqa: E402
from app.orchestrator.pipeline import floor_gate_verdicts  # noqa: E402

BLOCKING_W = 0.7

PER_ZONE = {
    "payment_critical": {"w": 0.95, "cells_total": 20, "cells_scored": 20, "worst_score": 0.0},
    "catch_all": {"w": 0.20, "cells_total": 43, "cells_scored": 43, "worst_score": 0.0},
}

FLOORS = {
    "payment_critical": {
        "required_min_score": 1.0,
        "worst_score": 0.0,
        "worst_band": "unknown",
        "meets_floor": False,
        "reason": "đường tiền thật",
    },
    "catch_all": {
        "required_min_score": 0.2,
        "worst_score": 0.0,
        "worst_band": "unknown",
        "meets_floor": False,
        "reason": "zone rẻ tiền",
    },
}


def _verdicts():
    return floor_gate_verdicts(FLOORS, PER_ZONE, blocking_w=BLOCKING_W)


def test_every_zone_becomes_a_gate_verdict() -> None:
    assert [v.gate for v in _verdicts()] == [GateName.GRID, GateName.GRID]


def test_only_a_blocking_zone_blocks() -> None:
    """Zone dưới sàn nhưng w thấp vẫn là `fail` — chỉ không phải một cái CHẶN.

    Gộp hai chuyện đó biến mọi ô lạ thành trạng thái chặn, và cái chặn nào cũng
    đỏ thì không ai đọc nữa (lý do ghi thẳng trong `floor.yaml::catch_all`).
    """
    by_zone = {v.reason.split()[1]: v for v in _verdicts()}
    assert by_zone["payment_critical"].blocked is True
    assert by_zone["catch_all"].blocked is False
    assert all(v.verdict == "fail" for v in _verdicts())


def test_a_failing_zone_carries_a_finding_anchored_to_the_policy_file() -> None:
    """Sàn là CHÍNH SÁCH: chỗ phải sửa là tệp khai sàn, không phải một tệp mã."""
    finding = _verdicts()[0].findings[0]
    assert finding.file.endswith("floor.yaml")
    assert finding.rule_id == "FLOOR-ZONE-BELOW-MIN"
    assert "Lý do sàn" in finding.finding


def test_severity_separates_blocking_from_advisory() -> None:
    by_zone = {v.reason.split()[1]: v for v in _verdicts()}
    assert by_zone["payment_critical"].findings[0].severity == "error"
    assert by_zone["catch_all"].findings[0].severity == "warn"


def test_a_zone_that_meets_its_floor_has_no_findings() -> None:
    floors = {"catch_all": {**FLOORS["catch_all"], "meets_floor": True, "worst_score": 1.0}}
    per_zone = {"catch_all": PER_ZONE["catch_all"]}
    verdict = floor_gate_verdicts(floors, per_zone, blocking_w=BLOCKING_W)[0]
    assert verdict.verdict == "pass"
    assert verdict.findings == []


def test_the_denominator_is_the_number_of_scoreable_cells() -> None:
    """`denominator == 0` là ĐỎ ở tầng UI, nên nó phải mang số ô thật."""
    assert [v.denominator for v in _verdicts()] == [43, 20]


@pytest.mark.parametrize("zone_id", ["payment_critical", "catch_all"])
def test_a_zone_missing_from_per_zone_is_a_crash_not_a_default(zone_id: str) -> None:
    """Index thẳng, không `.get(..., mặc_định)`.

    Một zone rời khỏi `per_zone` mà vẫn có phán quyết sàn nghĩa là hai nguồn đã
    trôi khỏi nhau; đọc bằng default sẽ cho zone đó điểm 0.0 im lặng.
    """
    partial = {k: v for k, v in PER_ZONE.items() if k != zone_id}
    with pytest.raises(KeyError):
        floor_gate_verdicts(FLOORS, partial, blocking_w=BLOCKING_W)


# ── claim bị từ chối ───────────────────────────────────────────────────────


def test_rejected_claims_have_their_own_field_not_a_silent_drop() -> None:
    resp = AnalyzeResponse(
        run_id="r",
        trace_id="t",
        target="shopcart",
        coverage={"risk_weighted": {}, "per_zone": []},
        rejected_claims=[
            RejectedClaim(
                id="c1",
                text="Line coverage là 156/160.",
                label="OBSERVED",
                reason="claim 'c1': OBSERVED mà không có anchor",
            )
        ],
    )
    assert resp.claims == []
    assert resp.rejected_claims[0].id == "c1"
    # Câu bị từ chối KHÔNG được lẫn vào claims — nó là tang vật, không phải kết luận.
    assert all(c.claim.id != "c1" for c in resp.claims)


def test_an_empty_rejected_list_is_the_normal_state() -> None:
    resp = AnalyzeResponse(
        run_id="r", trace_id="t", target="x", coverage={"risk_weighted": {}, "per_zone": []}
    )
    assert resp.rejected_claims == []
