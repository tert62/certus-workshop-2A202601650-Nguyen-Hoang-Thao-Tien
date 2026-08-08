"""Luồng phân tích: từ một thư mục mã nguồn tới một phán quyết.

Tệp này là chỗ DUY NHẤT biết thứ tự các bước. Mọi module bên dưới cố tình không
biết mình đứng ở bước nào — nhờ vậy đổi thứ tự là sửa một tệp, không phải sửa
bảy tệp.

Ba luật của luồng:

1. **Bước sau không được chạy trên đầu vào rỗng của bước trước.** Không có
   nhánh "không có dữ liệu thì coi như đạt".
2. **Mọi tỉ lệ sinh ra ở đây đều đi kèm k, n và khoảng.** Một `float` trần rời
   khỏi tệp này là một lỗi hợp đồng.
3. **Việc tính toán là của code, việc diễn giải là của mô hình.** Mô hình không
   bao giờ được hỏi "bao nhiêu ô", chỉ được hỏi "con số này nghĩa là gì".
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent.claims import ClaimParseError, extract_claims_json, parse_claims
from app.agent.context import load_prompt, render_prompt
from app.agent.llm import LLMClient
from app.agent.persona import PersonaStore
from app.agent.retrieval import build_context
from app.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ClaimOut,
    CoverageOut,
    RateOut,
    RejectedClaim,
    StreamEvent,
    ZoneOut,
)
from app.contracts.errors import CassetteMissError, CertusError, ConfigError
from app.contracts.types import (
    Band,
    Cell,
    Claim,
    EvidenceTier,
    Finding,
    GateName,
    GateVerdict,
    Interval,
)
from app.core.grid.cells import cell_id as make_cell_id
from app.core.grid.cells import enumerate_t_wise
from app.core.grid.project import project_cell
from app.core.grid.rollup import (
    evaluate_floor,
    load_band_scores,
    load_floors,
    min_per_zone,
    risk_weighted_coverage,
)
from app.core.grid.zones import load_zone_config, require_zone
from app.core.stats.intervals import interval
from app.orchestrator.observe import (
    load_mutation_artifact,
    lookup,
    mutation_counts,
    mutation_run_for_cell,
    observations_for_cells,
    scan_tests,
)
from app.observability import tracing
from app.observability.logging import get_logger
from app.policy.data_policy import load_policy
from app.settings import Settings, settings as default_settings

log = get_logger()


# --------------------------------------------------------------------------
# Khám phá trục
# --------------------------------------------------------------------------


@dataclass
class Axes:
    """Các trục rủi ro tìm được trong repo, kèm nguồn gốc.

    `source` không phải trang trí: một trục suy ra từ Enum trong code có tư cách
    khác hẳn một trục do mô hình đề xuất, và cổng đối xử với chúng khác nhau.
    """

    values: dict[str, list[str]] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)

    def size(self) -> int:
        return len(self.values)


_ENUM_HINTS = ("Tier", "Zone", "Method", "Type", "Status", "Kind", "Mode", "Level")

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _axis_name(class_name: str) -> str:
    """Tên trục = snake_case của tên Enum, KHÔNG phải `lower()` trần.

    `lower()` nuốt ranh giới từ: `PaymentMethod` → `paymentmethod`, trong khi
    `zones.yaml` (và mọi predicate zone do người viết) dùng `payment_method`.
    Hai cách viết cùng một trục là cách kinh điển để tập chặn IM LẶNG rỗng —
    predicate không khớp key nào nên mọi ô rơi về catch-all, và không có gì
    báo rằng zone rủi ro chưa từng được điền.
    """
    return _CAMEL_BOUNDARY.sub("_", class_name).lower()


def discover_axes(root: Path) -> Axes:
    """Đọc Enum trong mã nguồn để dựng trục. Deterministic, không hỏi mô hình.

    Trục là MẪU SỐ của toàn bộ phần chấm điểm phía sau. Để mô hình sinh mẫu số
    nghĩa là mẫu số đổi giữa hai lượt chạy trên cùng một repo, và mọi tỉ lệ phía
    sau mất khả năng so sánh.
    """
    import ast

    axes = Axes()
    for py in sorted(root.rglob("*.py")):
        if "test" in py.parts or py.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
            is_enum = any("Enum" in b for b in bases) or node.name.endswith(_ENUM_HINTS)
            if not is_enum:
                continue
            members = [
                t.id.lower()
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                for t in stmt.targets
                if isinstance(t, ast.Name) and not t.id.startswith("_")
            ]
            if len(members) >= 2:
                axis = _axis_name(node.name)
                axes.values[axis] = members
                axes.source[axis] = f"{py.relative_to(root)}::{node.name}"
    return axes


def restrict_axes(axes: Axes, confirmed: Mapping[str, Sequence[str]]) -> Axes:
    """Thu hẹp trục KHÁM PHÁ ĐƯỢC xuống tập người dùng XÁC NHẬN (HITL).

    Chỉ giữ trục nằm trong CẢ hai (khám phá ∩ xác nhận): người dùng CHỌN trong
    số trục code đã suy ra, KHÔNG thêm được trục không có Enum nào đứng sau — mẫu
    số phải neo vào code, đó là lý do `discover_axes` không hỏi mô hình. Với mỗi
    trục giữ lại, lọc member theo danh sách xác nhận nhưng GIỮ THỨ TỰ KHÁM PHÁ
    (axis-lock ổn định); danh sách rỗng ⇒ giữ mọi member. Trục còn dưới 2 giá trị
    bị bỏ hẳn — một trục một-giá-trị không phân biệt được gì, giữ lại chỉ làm hỏng
    phép đếm t-wise. `source` ghi thêm 'human-confirmed' để cổng phân biệt trục đã
    qua HITL với trục thô.
    """
    out = Axes()
    for axis, members in axes.values.items():
        if axis not in confirmed:
            continue
        want = list(confirmed[axis])
        kept = [m for m in members if m in want] if want else list(members)
        if len(kept) < 2:
            continue
        out.values[axis] = kept
        out.source[axis] = f"human-confirmed · {axes.source.get(axis, '?')}"
    return out


def select_axes(
    discovered: Axes,
    rules: Sequence[Mapping[str, Any]],
    *,
    confirmed_axes: Mapping[str, Sequence[str]] | None = None,
    candidates: Sequence[Any] | None = None,
) -> tuple[Axes, dict[str, Any]]:
    """Chọn tập trục bằng engine ToT (đề xuất → admit → beam), THAY discover-giữ-hết.

    Ba nhánh, đúng một chạy:
    - `confirmed_axes` có ⇒ NGƯỜI đã chốt (HITL): thu hẹp UNIVERSE ứng viên về đúng
      lựa chọn của họ. Ý người thắng beam.
    - ngược lại chạy beam ρ trên ứng viên; admit loại `asserted` (branch) khỏi default,
      giữ `retrieved`/`derived`. Beam tỉa nhiễu ở đâu zones có tín hiệu.
    - SÀN VIABILITY: beam lock < 2 trục (zones không mã hoá rủi ro repo này → ρ=0)
      ⇒ rơi về TẬP ENUM khám phá (mẫu số sạch nhất, không rơi về branch asserted).

    `candidates`: tập ứng viên ĐA NGUỒN (enum+config+branch — `axis_sources`). None ⇒
    chỉ Enum, dựng từ `discovered` — đường REPO MẪU: universe = discovered nên axis_lock/
    cell_id y hệt baseline → cassette/golden bất biến. Có ⇒ đường REPO THẬT.

    LUÔN dựng lại theo THỨ TỰ candidate (enum→config→branch); Enum-only ⇒ thứ tự này
    trùng thứ tự khám phá, nên repo có tập trục không đổi ra cell_id y hệt.
    """
    from app.core.grid.axis_admit import AxisCandidate, load_admit_config
    from app.core.grid.axis_score import load_search_params
    from app.core.grid.axis_search import search_axes

    # Enum-only mặc định: MỘT nguồn sự thật cho tập Enum + thứ tự khám phá.
    if candidates is None:
        candidates = [
            AxisCandidate(
                name=n, members=tuple(v), ref=discovered.source.get(n, "?"),
                tier="retrieved", origin="enum",
            )
            for n, v in discovered.values.items()
        ]

    # Universe = mọi ứng viên dựng thành Axes theo thứ tự candidate. HITL thu hẹp trên
    # ĐÂY (không chỉ Enum) nên người dùng chốt được cả trục config/branch.
    universe = Axes()
    for c in candidates:
        universe.values[c.name] = list(c.members)
        universe.source[c.name] = c.ref

    if confirmed_axes is not None:
        axes = restrict_axes(universe, confirmed_axes)
        return axes, {"engine": "hitl", "quarantined": [], "rejected": []}

    result = search_axes(
        candidates, fixed_axes={}, rules=rules,
        params=load_search_params(), admit_config=load_admit_config(),
    )
    locked = set(result.locked_axes)
    floored = len(locked) < 2
    if floored:
        locked = set(discovered.values)

    axes = Axes()
    order: Sequence[tuple[str, Sequence[str]]] = (
        list(discovered.values.items()) if floored
        else [(c.name, c.members) for c in candidates]
    )
    for name, members in order:
        if name in locked and name not in axes.values:
            axes.values[name] = list(members)
            axes.source[name] = universe.source.get(name, discovered.source.get(name, "?"))
    meta = {
        "engine": "floor" if floored else "tot",
        "quarantined": [q.axis for q in result.quarantined],
        "rejected": [{"axis": a, "reason": r} for a, r in result.rejected],
    }
    return axes, meta


def candidates_for(root: Path, discovered: Axes, *, is_sample: bool) -> list[Any] | None:
    """Ứng viên đa nguồn cho REPO THẬT; None cho REPO MẪU.

    Repo mẫu (`req.target`, dưới targets_dir) phải ra tập trục y HỆT baseline để
    cassette/golden bất biến — nên KHÔNG bơm config/branch, giữ Enum-only. Repo thật
    (`req.upload_id`, dưới workspace_dir) bật đa nguồn để engine + HITL có gì mà tỉa;
    repo thật KHÔNG có cassette (chạy live) nên đổi tập trục không phá gì.
    """
    if is_sample:
        return None
    from app.core.grid.axis_sources import propose_candidates

    return propose_candidates(root, discovered.values, discovered.source)


# --------------------------------------------------------------------------
# Quan sát
# --------------------------------------------------------------------------


def observe_cells(
    axes: Axes,
    *,
    zones: list[Mapping[str, Any]],
    blocking_w: float,
    observations: Mapping[tuple[str, ...], Mapping[str, Any]] | None = None,
    mutation: Mapping[str, Any] | None = None,
) -> list[Cell]:
    """Liệt kê ô rồi chiếu band cho từng ô.

    Ô không có quan sát nào KHÔNG biến mất — nó thành `unknown`. Ô biến mất là
    ô rời khỏi mẫu số, và mẫu số nhỏ đi thì mọi tỉ lệ đẹp lên mà không ai làm gì.

    `mutation` là artifact mutation precomputed (hoặc None). Nó chỉ được tiêm vào
    ô THUỘC zone mà artifact khai, và chỉ khi ô ấy đã có quan sát — một ô chưa có
    test không thể được nâng band bằng một verdict mutation.
    """
    if axes.size() < 2:
        raise CertusError(
            f"chỉ tìm được {axes.size()} trục rủi ro — dưới 2 trục thì không có "
            "cặp nào để phủ, và mọi con số grid phía sau vô nghĩa"
        )
    obs = observations or {}
    # Axis lock = thứ tự KHÁM PHÁ, không phải sorted. discover_axes duyệt tệp
    # theo đường dẫn đã sắp nên thứ tự này ổn định giữa hai lượt chạy, và
    # enumerate_t_wise sinh tên trục theo đúng thứ tự đó.
    axis_lock = list(axes.values)
    cells: list[Cell] = []
    for names, values in enumerate_t_wise(axes.values, t=2):
        cid = make_cell_id((names, values), axis_lock)
        combo = dict(zip(names, values, strict=True))
        zone = require_zone(combo, zones)
        observation = lookup(obs, values) if obs else None
        if mutation is not None and observation is not None:
            mrun = mutation_run_for_cell(
                mutation, zone_id=str(zone["id"]), cell_id=cid
            )
            if mrun is not None:
                # Bản sao — không vá vào bảng quan sát dùng chung: cùng một
                # value-tuple có thể tra ra cho nhiều ô, và chỉ ô đúng zone mới
                # được mang mutation_run.
                observation = {
                    **observation,
                    "mutation_run": mrun,
                    "calibration_seed_id": mrun["seed_id"],
                }
        cells.append(
            project_cell(
                cell_id=cid,
                axes=combo,
                zone=zone,
                blocking_w=blocking_w,
                observation=observation,
            )
        )
    return cells


def _analyze_nonce(req: AnalyzeRequest) -> str:
    """Nonce TẤT ĐỊNH theo (repo, câu hỏi).

    Nonce đi vào prompt, prompt đi vào `cassette_key` (hash cả messages). Một
    nonce ngẫu nhiên làm MỌI lượt chạy ra một khoá khác nhau → cassette không
    bao giờ hit → replay chết, và cả kiến trúc cassette-mặc-định sụp. Tất định
    theo (target/upload_id, question) giữ replay chạy được, mà vẫn là một giá
    trị mô hình phải chép lại đúng để chứng minh nó đã đọc khối chỉ thị này.
    """
    seed = f"{req.target or req.upload_id}|{req.question}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _format_artifacts(coverage: CoverageOut) -> str:
    """Gói các con số ĐÃ TÍNH thành khối chữ cho mô hình DIỄN GIẢI.

    Luật số 3 của luồng: model đọc số, không tính số. Ba tầng mẫu số đứng cạnh
    nhau và không gộp — đúng thứ prompt dặn model giữ nguyên.

    KHỐI NÀY LÀ MỘT HỢP ĐỒNG BẤT BIẾN, không phải một chỗ để bổ sung dần. Nó đi
    vào `messages`, `messages` đi vào `cassette_key` (sha256 của model + system +
    messages + tools). Thêm MỘT dòng ở đây là đổi khoá của toàn bộ 10 cassette
    trong `fixtures/cassettes/` → mọi lượt chạy mock trượt cassette → cả lớp mất
    phần diễn giải. `coverage.mutation` vì vậy CÓ trong response (UI và CLI in
    đủ ba mẫu số) nhưng CHƯA có ở đây: muốn model nói về nó thì phải thêm dòng
    và THU LẠI cassette trong cùng một lượt (`CERTUS_LLM_MODE=record`), không
    làm được nửa vời.
    """
    lines: list[str] = []
    if coverage.line is not None:
        iv = coverage.line.interval
        lines.append(
            f"- Line coverage: {coverage.line.k}/{coverage.line.n} = "
            f"{coverage.line.point:.1%}, Wilson [{iv.lower:.3f}, {iv.upper:.3f}]"
        )
    lines.append(
        f"- Grid coverage: {coverage.grid.k}/{coverage.grid.n} = "
        f"{coverage.grid.point:.1%} ô rủi ro đã phủ"
    )
    lines.append(
        f"- Ô N/A: {coverage.cells_na} · ô unknown: {coverage.cells_unknown} · "
        f"tổng {coverage.cells_total} ô"
    )
    for z in coverage.per_zone:
        lines.append(
            f"- Zone {z.zone_id} (w={z.weight}): score={z.score:.3f} trên "
            f"{z.cells_scoreable}/{z.cells_total} ô chấm được"
        )
    return "\n".join(lines)


#: Tệp mà mọi finding của cổng sàn neo về. Sàn là CHÍNH SÁCH, nên chỗ phải sửa
#: khi một zone đỏ là tệp khai sàn, không phải tệp mã nào.
_FLOOR_SOURCE = "src/backend/config/floor.yaml"


def floor_gate_verdicts(
    floor_verdicts: Mapping[str, Mapping[str, Any]],
    per_zone: Mapping[str, Mapping[str, Any]],
    *,
    blocking_w: float,
) -> list[GateVerdict]:
    """Đổi phán quyết sàn per-zone thành `GateVerdict` — một cổng cho mỗi zone.

    Vì sao cần hàm này: `AnalyzeResponse.gates` từng là `[]` HẰNG SỐ trong khi
    luồng đã chấm sàn cho từng zone và phát ra dưới dạng sự kiện SSE thô. Hệ quả
    đo được: `GateChain` trên UI chỉ có dữ liệu ở chế độ mock, nên chuỗi cổng —
    thứ mà cả sản phẩm này lấy làm trung tâm — vô hình khi chạy với backend thật.

    Vì sao KHÔNG gọi `gates.registry.run_chain()` ở đây, dù registry tự khai
    "pipeline PHẢI cắm vào ở bước 7":

    * `requirements`, `design`, `execution`, `outcome` đọc tạo tác của một luồng
      REVIEW PR (tiêu chí nghiệm thu, ma trận thiết kế, diff, cửa sổ hậu triển
      khai). Luồng analyze không sinh ra cái nào. Truyền `None` vào không phải
      "bỏ qua" mà là `ctx.refuse()` → `verdict="fail", blocked=True`, tức MỌI
      lượt phân tích sẽ đỏ vì bốn lý do không liên quan gì tới repo người dùng.
      Một cái đỏ giả cũng hỏng hệt một cái xanh giả.
    * `gates.grid.check_grid` so mọi zone chặn với MỘT ngưỡng `grid.min_band_score
      = 1.0`, trong khi `floor.yaml` cố ý đặt sàn KHÁC NHAU cho từng zone và giải
      thích tại sao (`concurrency: 0.6` — "đòi high ở đây là đòi một bằng chứng
      mà cỗ máy hiện chưa sinh ra được"). Chạy nó ở đây là thi hành một chính
      sách mà tệp chính sách đã bác bỏ, có ghi lý do.

    Nên cổng của luồng analyze là cổng sàn per-zone, và nó ra ngoài bằng đúng
    kiểu `GateVerdict` như mọi cổng khác.
    """
    out: list[GateVerdict] = []
    for zone_id in sorted(floor_verdicts):
        v = floor_verdicts[zone_id]
        summary = per_zone[zone_id]
        meets = bool(v["meets_floor"])
        blocking = float(summary["w"]) >= blocking_w
        findings: list[Finding] = []
        if not meets:
            findings.append(
                Finding(
                    rule_id="FLOOR-ZONE-BELOW-MIN",
                    # Zone không chặn dưới sàn vẫn là một phát hiện, chỉ không
                    # phải một cái chặn — hạ severity, KHÔNG giấu finding.
                    severity="error" if blocking else "warn",
                    file=_FLOOR_SOURCE,
                    line=1,
                    finding=(
                        f"zone {zone_id!r}: ô tệ nhất band={v['worst_band']} "
                        f"score={v['worst_score']}, sàn đòi >= {v['required_min_score']}. "
                        f"Lý do sàn: {v['reason']}"
                    ),
                )
            )
        out.append(
            GateVerdict(
                gate=GateName.GRID,
                verdict="pass" if meets else "fail",
                evidence_tier=EvidenceTier.DERIVED,
                findings=findings,
                compare_op=">=",
                # Mẫu số là số ô CHẤM ĐƯỢC của zone. 0 ở đây nghĩa là zone không
                # còn ô nào để chấm — đỏ, không phải xanh (xem GateVerdict).
                denominator=int(summary["cells_scored"]),
                blocked=(not meets) and blocking,
                reason=(
                    f"zone {zone_id} (w={summary['w']}, "
                    f"{'chặn' if blocking else 'không chặn'}): "
                    f"worst={v['worst_score']} vs sàn {v['required_min_score']}"
                ),
            )
        )
    return out


def rate(name: str, k: int, n: int, *, conf: float = 0.95) -> RateOut:
    """Gói một tỉ lệ kèm mẫu số và khoảng. Không có lối đi tắt trả float trần."""
    if n <= 0:
        raise CertusError(
            f"tỉ lệ {name!r} có mẫu số 0 — không có phép tính nào cứu được điều đó"
        )
    iv = interval(k, n, conf=conf, method="wilson")
    flags: list[str] = []
    if n < 10:
        flags.append("n-too-small")
    if iv.upper - iv.lower > 0.30:
        flags.append("interval-wide")
    if getattr(iv, "saturated", False):
        flags.append("interval-saturated")
    return RateOut(
        name=name,
        k=k,
        n=n,
        point=k / n,
        interval=iv if isinstance(iv, Interval) else Interval(**dict(iv)),
        flags=flags,
    )


# --------------------------------------------------------------------------
# Luồng
# --------------------------------------------------------------------------

#: Chữ đọc được cho mỗi cờ. Hợp đồng bắt cảnh báo hiện thành CÂU, không phải
#: mã — một mã bắt người đọc tra bảng, và trong thực tế họ không tra.
_WARNING_TEXT = {
    "n-too-small": "mẫu số quá nhỏ để kết luận: khoảng tin cậy rộng hơn chính con số",
    "interval-wide": "khoảng tin cậy rộng hơn 30 điểm phần trăm — con số này chưa nói được gì",
    "interval-saturated": "khoảng đã tràn ra ngoài [0,1] rồi bị cắt về; đừng đọc nó như một khoảng hẹp",
}

STAGES = (
    "resolve_target",
    "apply_data_policy",
    "discover_axes",
    "run_tests",
    "read_coverage",
    "project_grid",
    "rollup",
    "run_gates",
    "explain",
)


@dataclass
class PipelineResult:
    response: AnalyzeResponse
    events: list[StreamEvent] = field(default_factory=list)


class Pipeline:
    """Chạy một lượt phân tích và phát sự kiện dọc đường.

    Được viết ở dạng async generator để UI thấy tiến trình thay vì một vòng
    xoay bốn phút. Một tiến trình không quan sát được thì mọi lỗi trong nó
    trông giống hệt nhau: "chậm".
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.cfg = settings or default_settings
        self._seq = 0

    def _event(self, kind: str, trace_id: str, **payload: Any) -> StreamEvent:
        self._seq += 1
        return StreamEvent(seq=self._seq, kind=kind, trace_id=trace_id, payload=payload)

    def _step(self, trace_id: str, name: str, status: str = "done", **extra: Any):
        """Một bước của luồng. `step` là số thứ tự trong STAGES, không phải số đếm
        tự do: hai lượt chạy phải đánh số cùng một bước giống nhau."""
        return self._event(
            "step", trace_id,
            step=STAGES.index(name) + 1 if name in STAGES else 0,
            name=name, status=status, **extra,
        )

    def _log(self, trace_id: str, level: str, msg: str):
        return self._event("log", trace_id, level=level, msg=msg)

    def resolve_target(self, req: AnalyzeRequest) -> Path:
        if bool(req.target) == bool(req.upload_id):
            raise CertusError(
                "phải có ĐÚNG một trong `target` và `upload_id`. Nhận cả hai (hoặc "
                "không cái nào) là để ngỏ chuyện nội dung người dùng tải lên được "
                "đối xử như repo mẫu đáng tin"
            )
        if req.target:
            root = (self.cfg.targets_dir / req.target).resolve()
            if self.cfg.targets_dir.resolve() not in root.parents:
                raise CertusError(f"target {req.target!r} nằm ngoài thư mục repo mẫu")
        else:
            root = (self.cfg.workspace_dir / str(req.upload_id)).resolve()
            # Nhánh `target` kiểm chứa, nhánh này thì KHÔNG — cùng một lỗ hổng
            # đã vá cho `target`: `upload_id="../../fixtures/targets/payments"`
            # resolve() thoát khỏi workspace rồi pytest chạy trên thư mục bất kỳ
            # (→ code-exec). upload_id là chuỗi client gửi tự do, phải bị chặn
            # đúng như target.
            if self.cfg.workspace_dir.resolve() not in root.parents:
                raise CertusError(
                    f"upload_id {req.upload_id!r} nằm ngoài thư mục workspace"
                )
        if not root.is_dir():
            raise CertusError(f"không có thư mục {root}")
        return root

    async def run(self, req: AnalyzeRequest) -> AsyncIterator[StreamEvent]:
        started = time.time()
        final: dict[str, Any] | None = None
        with tracing.start_trace() as trace_id:
            try:
                async for ev in self._run_inner(req, trace_id):
                    # `done` phải đếm claims thật và blocked thật, không phải
                    # hằng số 0/False như khi tầng agent còn chưa nối. Đọc lại
                    # từ step `explain` cuối cùng — chỗ duy nhất giữ response.
                    if (
                        ev.kind == "step"
                        and ev.payload.get("name") == "explain"
                        and "response" in ev.payload
                    ):
                        final = ev.payload["response"]
                    yield ev
            except CertusError as exc:
                # Lỗi có cấu trúc đi ra bằng đúng dòng stream, không nuốt.
                yield self._event(
                    "error", trace_id, code=type(exc).__name__, msg=str(exc)
                )
                return
            yield self._event(
                "done", trace_id,
                claims=len(final["claims"]) if final else 0,
                # Đi kèm ngay trong `done` chứ không đợi một lời gọi REST thứ hai:
                # số claim HIỂN THỊ mà không kèm số claim BỊ TỪ CHỐI là đúng cái
                # nửa sự thật khiến câu trả lời ngắn đi mà không ai biết vì sao.
                rejected_claims=final["rejected_claims"] if final else [],
                blocked=(final["verdict"] == "blocked") if final else False,
                elapsed_s=round(time.time() - started, 3),
            )

    async def _run_inner(
        self, req: AnalyzeRequest, trace_id: str
    ) -> AsyncIterator[StreamEvent]:
        yield self._log(trace_id, "INFO", f"bắt đầu phân tích {req.target or req.upload_id}")

        yield self._step(trace_id, "resolve_target", "running")
        root = self.resolve_target(req)
        yield self._step(trace_id, "resolve_target", path=str(root))

        # Repo THẬT (upload) phải qua bước chọn trục. Proposer đa nguồn trên repo lạ
        # thường floor về rất nhiều trục Enum (zones là của repo mẫu), auto-analyze sẽ
        # nổ cartesian và mọi con số grid vô nghĩa. Buộc HITL: người chốt 2–4 trục qua
        # /axes/discover rồi gửi confirmed_axes. Repo mẫu (target) miễn — trục cố định.
        if req.upload_id and not req.confirmed_axes:
            raise CertusError(
                "repo tải lên phải CHỌN TRỤC trước khi phân tích: gọi "
                "/api/axes/discover, chốt 2–4 trục rồi gửi lại kèm confirmed_axes. "
                "(Repo mẫu thì không cần — trục đã cố định để bài giảng tất định.)"
            )

        # Chính sách dữ liệu chạy TRƯỚC mọi thứ chạm tới mô hình.
        policy = load_policy()
        sent, held = [], {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root))
            decision = policy.decide(rel)
            (sent.append(rel) if decision.allowed else held.setdefault(rel, decision.reason))
        yield self._step(
            trace_id, "apply_data_policy",
            files_sent=len(sent), files_held=len(held),
        )

        # load_zone_config đã compile sẵn — không ai được cầm rules chưa compile.
        # Nạp TRƯỚC chọn trục: engine ρ cần rules zone để chấm mật độ rủi ro.
        zone_cfg = load_zone_config()
        blocking_w = zone_cfg.blocking_w
        cfg_zones = zone_cfg.rules

        # Chọn trục bằng engine ToT (thay discover-giữ-hết). `confirmed_axes` (nếu
        # có) là ý NGƯỜI sau khi xem đề xuất — thắng beam. Không có ⇒ beam ρ + sàn
        # viability. select_axes giữ THỨ TỰ KHÁM PHÁ nên repo có tập trục không đổi
        # (mọi repo mẫu hiện tại) ra cell_id y hệt → cassette/golden bất biến.
        discovered = discover_axes(root)
        candidates = candidates_for(root, discovered, is_sample=bool(req.target))
        axes, axis_meta = select_axes(
            discovered, cfg_zones, confirmed_axes=req.confirmed_axes, candidates=candidates
        )
        yield self._step(
            trace_id, "discover_axes",
            axes={k: len(v) for k, v in axes.values.items()},
            source=axes.source, **axis_meta,
        )
        tests = scan_tests(root)
        suite_exit, cov_suite, (lines_hit, lines_total) = run_target_suite(root)
        yield self._step(
            trace_id, "run_tests",
            test_functions=len(tests), exit_code=suite_exit,
            lines_covered=len(cov_suite),
            lines_hit=lines_hit, lines_total=lines_total,
        )
        # `read_coverage` là bước 5 trong STAGES. Nó gộp VẬT LÝ vào run_tests (cùng
        # một `run_target_suite`), nhưng vẫn phát THÀNH bước riêng — nếu không, sidebar
        # hiện "chưa chạy" cho một bước thực sự đã chạy, đúng cái nhầm mà StepProgress
        # được viết ra để tránh.
        yield self._step(
            trace_id, "read_coverage",
            lines_hit=lines_hit, lines_total=lines_total,
        )
        obs_table = observations_for_cells(
            tests,
            code_path=self.cfg.__dict__.get("entry_symbol", "checkout"),
            cov_suite=cov_suite,
            suite_exit_code=suite_exit,
        )
        # Mutation replay: chỉ cho repo mẫu (target), không cho upload — upload
        # chưa từng qua lượt mutmut của host nên KHÔNG có artifact, và đó là
        # fail-closed đúng, không phải thiếu sót.
        mutation_artifact = (
            load_mutation_artifact(req.target, self.cfg.mutations_dir)
            if req.target
            else None
        )
        cells = observe_cells(
            axes,
            zones=cfg_zones,
            blocking_w=blocking_w,
            observations=obs_table,
            mutation=mutation_artifact,
        )
        for c in cells[:200]:
            yield self._event("cell", trace_id, cell=c.model_dump(mode="json"))
        yield self._step(trace_id, "project_grid", cells=len(cells))

        band_scores = load_band_scores()
        rw = risk_weighted_coverage(cells, band_scores)
        per_zone = min_per_zone(cells, band_scores)
        yield self._step(trace_id, "rollup", risk_weighted=rw, zones=len(per_zone))

        covered = sum(1 for c in cells if c.band in (Band.HIGH, Band.MED))
        scoreable = sum(1 for c in cells if c.band is not Band.NA)
        grid_rate = rate("grid_coverage", covered, max(scoreable, 1))

        line_rate = (
            rate("line_coverage", lines_hit, lines_total) if lines_total else None
        )

        # Mẫu số thứ ba. Vắng artifact ⇒ None, và None hiện ra là "không có dòng
        # mutation" chứ không phải 0% — thiếu bằng chứng khác hẳn bằng chứng xấu.
        mcounts = mutation_counts(mutation_artifact)
        mutation_rate = (
            rate("mutation_score", mcounts[0], mcounts[1]) if mcounts else None
        )

        coverage = CoverageOut(
            line=line_rate,
            mutation=mutation_rate,
            grid=grid_rate,
            risk_weighted=rw,
            per_zone=[
                ZoneOut(
                    zone_id=zid,
                    weight=float(z["w"]),
                    # Khoá phải khớp ĐÚNG cái min_per_zone trả ra (worst_score,
                    # cells_scored). Đọc `.get("min_score", 0.0)` từng nuốt lỗi
                    # bằng default: sai khoá vẫn ra 0.0 nên score mọi zone luôn
                    # 0.0 mà không ai thấy. Index thẳng để sai khoá thì nổ.
                    score=float(z["worst_score"]),
                    cells_total=int(z["cells_total"]),
                    cells_scoreable=int(z["cells_scored"]),
                )
                for zid, z in per_zone.items()
            ],
            cells=cells,
            cells_total=len(cells),
            cells_na=sum(1 for c in cells if c.band is Band.NA),
            cells_unknown=sum(1 for c in cells if c.band is Band.UNKNOWN),
            confidence=grid_rate.point,
        )

        # ── giai đoạn DIỄN GIẢI: mô hình ĐỌC số, code đã TÍNH xong ──────────
        # Đây là chỗ DUY NHẤT tầng agent chạm pipeline. Khối này từng bị bỏ
        # trống (claims=[], gates=[], verdict="inconclusive"), nên mọi lỗi sống
        # ở đường LLM→claim (confabulation, truncation, nhãn-từ-tool, tất định,
        # cá nhân hoá) không có đường nào biểu hiện. Lỗi ở bước này KHÔNG làm
        # hỏng phần số: coverage vẫn ra, chỉ phần diễn giải khuyết kèm cảnh báo.
        yield self._step(trace_id, "explain", "running")
        nonce = _analyze_nonce(req)
        kb_context = build_context(req.question)
        with PersonaStore(settings=self.cfg) as pstore:
            persona = pstore.persona_block(req.user_id)
        prompt = render_prompt(
            "analyze",
            QUESTION=req.question,
            KB_CONTEXT=kb_context or "(không đoạn KB nào khớp câu hỏi)",
            ARTIFACTS=_format_artifacts(coverage),
            PERSONA=persona or "(chưa có ngữ cảnh cá nhân hoá cho người dùng này)",
            NONCE=nonce,
        )
        system = load_prompt("system")
        # KHÔNG truyền tools vào lượt gọi này — CÓ CHỦ ĐÍCH. Bước diễn giải là
        # single-shot để cassette tất định (1000 sinh viên replay đồng thời, một
        # khoá ổn định cho mỗi (repo, câu hỏi)). Model THẬT được cấp tool sẽ dừng
        # ở `stop_reason=tool_use` chờ kết quả rồi mới nói tiếp — mà luồng này
        # không có vòng lặp tool, nên nó dừng với text RỖNG và không claim nào ra.
        # Prompt vẫn khung tool về mặt khái niệm (bug "only a tool promotes a
        # claim" nằm ở chỗ model TỰ dán nhãn OBSERVED không có tool nào phong),
        # và có sẵn nhánh "nếu tool không khả dụng thì tự tính" — nên model tính
        # trực tiếp rồi trả đúng một object JSON như phần 'Định dạng trả lời' đòi.

        claims: list[Claim] = []
        rejected: list[RejectedClaim] = []
        llm_warnings: list[str] = []
        span = tracing.llm_span("analyze.explain")
        client = LLMClient(self.cfg)
        try:
            resp = await client.complete(
                system=system,
                messages=[{"role": "user", "content": prompt}],
                cassette_hint="analyze",
                # Mồi `{` để ép mọi model (kể cả Haiku yếu) tiếp nối thành đúng
                # object JSON như "Định dạng trả lời" đòi, thay vì đáp văn xuôi rồi
                # rớt về cảnh báo "không đọc được câu trả lời". Chỉ tác động đường
                # API thật; mock/cassette của lớp không đổi khoá.
                prefill="{",
            )
            for chunk in resp.chunks or ([resp.text] if resp.text else []):
                yield self._event("token", trace_id, text=chunk)
            span.finish(
                status="ok", from_cassette=resp.from_cassette,
                model=resp.model, stop_reason=resp.stop_reason,
                tool_uses=len(resp.tool_uses),
            )
            parsed = extract_claims_json(resp.text)
            if str(parsed.get("nonce")) != nonce:
                # Nonce là cửa chống trả-lời-lạc: câu trả lời không chép đúng
                # nonce có thể là của một prompt khác (hoặc bị bơm), không đọc.
                llm_warnings.append(
                    "câu trả lời của mô hình thiếu hoặc sai nonce — bỏ không đọc"
                )
            else:
                # parse_claims dựng bằng model_construct (bỏ validator — đó là
                # bug nhãn-từ-tool). Pipeline TUYỆT ĐỐI không được sập vì một
                # câu trả lời dị dạng: validate lại từng claim, cái nào validator
                # của chính hệ từ chối thì loại kèm cảnh báo (chính sự bất nhất
                # "parser nhận, validator chối" là thứ tầng B cần phơi), cái nào
                # hợp lệ — kể cả OBSERVED không do tool phong — vẫn tới output.
                for c in parse_claims(parsed):
                    try:
                        Claim.model_validate(c.model_dump())
                    except ValidationError as exc:
                        detail = exc.errors()[0].get("msg", "") if exc.errors() else ""
                        llm_warnings.append(
                            f"claim {c.id!r} dị dạng, không hiển thị: {detail}"
                        )
                        # Loại khỏi `claims` NHƯNG không xoá khỏi lịch sử: câu bị
                        # từ chối đi ra bằng `rejected_claims` để claim inspector
                        # cho người đọc thấy quy tắc đang thi hành, thay vì thấy
                        # một đoạn văn ngắn hơn mà không biết vì sao.
                        rejected.append(
                            RejectedClaim(
                                id=c.id, text=c.text, label=c.label.value, reason=detail
                            )
                        )
                        continue
                    claims.append(c)
        except CassetteMissError:
            span.finish(status="error", error="cassette_miss")
            llm_warnings.append(
                "chưa có cassette cho bước diễn giải ở repo/câu hỏi này; chạy một "
                "lượt `CERTUS_LLM_MODE=record` để sinh, hoặc đặt CERTUS_LLM_MODE=live "
                "kèm khoá API"
            )
        except (ClaimParseError, ConfigError) as exc:
            span.finish(status="error", error=type(exc).__name__)
            llm_warnings.append(f"không đọc được câu trả lời của mô hình: {exc}")
        tracing.emit(span)
        yield self._event("span", trace_id, span=span.to_row().to_sse())
        for c in claims:
            yield self._event("claim", trace_id, claim=c.model_dump(mode="json"))

        # ── gate: sàn grid per-zone — cổng tự nhiên của luồng analyze ───────
        floor_verdicts = evaluate_floor(per_zone, load_floors())
        blocking_failures = [
            zid for zid, v in floor_verdicts.items()
            if per_zone[zid]["w"] >= blocking_w and not v["meets_floor"]
        ]
        gate_verdicts = floor_gate_verdicts(
            floor_verdicts, per_zone, blocking_w=blocking_w
        )
        # Sự kiện `gate` mang ĐÚNG `GateVerdict`, đúng như `types/sse.ts` khai từ
        # đầu (`{ event: 'gate'; data: GateVerdict }`). Trước đây nó mang dict sàn
        # thô (`meets_floor`, `required_min_score`…) — một hình dạng thứ hai cho
        # cùng một tên sự kiện, nên `GateChain` phải lọc bỏ mọi thứ không có mảng
        # `findings` và cuối cùng không hiển thị gì khi chạy backend thật.
        for gv in gate_verdicts:
            yield self._event("gate", trace_id, **gv.model_dump(mode="json"))
        blocked = bool(blocking_failures)
        verdict = "blocked" if blocked else ("pass" if claims else "inconclusive")
        # `run_gates` là bước 8 trong STAGES: chuỗi cổng sàn per-zone vừa chạy ở
        # trên. Trước đây nó chỉ phát sự kiện `gate` cho từng zone mà KHÔNG phát
        # `step`, nên sidebar hiện bước 8 "chưa chạy" dù cổng đã chấm.
        yield self._step(
            trace_id, "run_gates",
            zones=len(floor_verdicts),
            blocking_failures=len(blocking_failures),
            verdict=verdict,
        )

        # Cảnh báo giữ tệp phải NÊU RÕ tệp nào — "1 tệp bị giữ lại" không cho người
        # đọc biết gì để đối chiếu với danh sách "đã gửi". `held` là {đường-dẫn:
        # lý-do}; liệt kê đường dẫn (kèm lý do khi đủ chỗ).
        held_warning = (
            f"{len(held)} tệp bị giữ lại theo chính sách dữ liệu: "
            + "; ".join(f"{p} ({r})" for p, r in sorted(held.items()))
            if held
            else None
        )
        response = AnalyzeResponse(
            run_id=trace_id,
            trace_id=trace_id,
            target=req.target or str(req.upload_id),
            coverage=coverage,
            claims=[ClaimOut(claim=c, supported_by=c.evidence_ids) for c in claims],
            rejected_claims=rejected,
            gates=gate_verdicts,
            verdict=verdict,
            files_sent_to_model=sent,
            warnings=([held_warning] if held_warning else []) + llm_warnings,
        )
        # Cảnh báo phải ra thành sự kiện riêng, không nằm lẫn trong response —
        # hợp đồng bắt mỗi cảnh báo hiện thành một DÒNG CHỮ trên UI, và một
        # trường trong JSON thì không có gì buộc UI phải hiển thị nó.
        for flag in (line_rate.flags if line_rate else []) + grid_rate.flags:
            yield self._event(
                "warning", trace_id, code=flag,
                msg=_WARNING_TEXT.get(flag, flag),
            )
        # Hai NGUỒN cảnh báo khác hẳn nhau, KHÔNG gộp chung mã: giữ tệp là chuyện
        # chính sách dữ liệu; còn "mô hình trả sai định dạng / nonce lệch / claim
        # dị dạng" là chuyện của bước diễn giải. Gắn `data-policy` cho nhóm sau thì
        # UI in nhầm tiêu đề "đã giữ tệp" lên một lỗi output của mô hình.
        if held_warning:
            yield self._event("warning", trace_id, code="data-policy", msg=held_warning)
        for w in llm_warnings:
            yield self._event("warning", trace_id, code="llm-output", msg=w)

        yield self._step(trace_id, "explain", response=response.model_dump(mode="json"))


async def analyze(req: AnalyzeRequest, settings: Settings | None = None) -> PipelineResult:
    """Chạy hết luồng và gom lại thành một kết quả — dùng cho CLI và test."""
    pipe = Pipeline(settings)
    events: list[StreamEvent] = []
    response: AnalyzeResponse | None = None
    async for ev in pipe.run(req):
        events.append(ev)
        if (
            ev.kind == "step"
            and ev.payload.get("name") == "explain"
            and "response" in ev.payload
        ):
            # Bước `explain` phát hai lần: `status="running"` (chưa có response)
            # rồi bước đóng kèm `response`. Chỉ đọc cái sau.
            response = AnalyzeResponse.model_validate(ev.payload["response"])
        if ev.kind == "error":
            raise CertusError(ev.payload["msg"])
    if response is None:
        raise CertusError("luồng kết thúc mà không sinh ra kết quả nào")
    return PipelineResult(response=response, events=events)


def run_target_suite(root: Path) -> tuple[int, set[str], tuple[int, int]]:
    """Chạy bộ kiểm của repo đích và đọc lại phần nó đã chạm.

    Đi qua `core/exec/runner` chứ không gọi `subprocess` thẳng: đó là chỗ duy
    nhất có allowlist và có ghi sổ bằng chứng. Một lối chạy thứ hai, dù chỉ để
    tiện, là một lối vòng qua cả hai thứ đó.
    """
    from app.core.exec.coverage_reader import read_coverage_data
    from app.core.exec.runner import run_probe

    result = run_probe(root, ["coverage", "run", "-m", "pytest", "-q"])
    # Probe BỊ CHẶN không phải một phép đo — xem `ProbeResult.blocked`. Trả về
    # (0, 0) ở đây từng làm chính CERTUS mắc lỗi mà CERTUS tồn tại để bắt: cùng
    # một repo cho `line 156/160 · grid 27/63` khi probe chạy được và
    # `không có dòng line · grid 0/63` khi probe bị chặn, KHÔNG một cảnh báo nào.
    # Người đọc con số thứ hai không có cách nào biết mình đang nhìn một sự cố
    # hạ tầng chứ không phải một bộ kiểm thưa.
    if result.blocked:
        raise CertusError(
            f"không chạy được bộ kiểm của repo: {result.block_reason}. "
            f"Lệnh: {result.command}. Đây là sự cố thực thi, không phải độ phủ "
            f"thấp — CERTUS từ chối phát biểu tỉ lệ nào trên một lượt chạy "
            f"chưa từng xảy ra."
        )

    data_file = root / ".coverage"
    if not data_file.exists():
        raise CertusError(
            f"lượt chạy kết thúc (exit {result.exit_code}) nhưng không sinh ra "
            f"{data_file.name}. Thiếu artifact độ phủ và độ phủ 0% là hai chuyện "
            f"khác nhau; kiểm tra repo có test nào được thu thập không."
        )

    # Cửa fail-closed của `coverage_reader._finish()`: report đọc được nhưng RỖNG
    # (quên --source, chỉ collect mà không sinh report, sai format) cho ra đúng
    # một triệu chứng là 0% "đọc như một phép đo tự tin". Để module đó từ chối
    # trước, rồi mới đếm — nó là chỗ ở duy nhất của luật "report rỗng thì nổ".
    read_coverage_data(data_file)

    covered: set[str] = set()
    lines_hit = 0
    lines_total = 0
    # Dùng `analysis2()` của chính coverage.py chứ không tự đếm câu lệnh: nó biết
    # dòng nào CHẠY ĐƯỢC, dòng nào bị `# pragma: no cover`, dòng nào chỉ là tiếp
    # nối của câu lệnh trước. Bản tự đếm bằng AST của tôi từng cho ra 444/444 =
    # 100% vì nhánh parse nổ rồi rơi về `total = hit` — mẫu số bằng tử số thì tỉ
    # lệ luôn đẹp. `read_coverage_data` ở trên KHÔNG thay được nó: `CoverageData`
    # chỉ lưu tập dòng ĐÃ CHẠY, nên mẫu số suy từ đó luôn bằng tử số.
    from coverage import Coverage

    cov = Coverage(data_file=str(data_file))
    cov.load()
    for measured in sorted(cov.get_data().measured_files()):
        name = Path(measured).stem
        if name.startswith("test_") or name == "conftest":
            # Bài kiểm không thuộc mẫu số của độ phủ: đo xem bài kiểm có tự chạy
            # hết chính nó không thì luôn ra gần 100%.
            continue
        _fn, statements, _excluded, missing, _fmt = cov.analysis2(measured)
        lines_total += len(statements)
        lines_hit += len(statements) - len(missing)
        covered.add(name)
        for line_no in set(statements) - set(missing):
            covered.add(f"{name}:{line_no}")

    # Tên hàm cấp module cũng được coi là "đã chạm", để `code_path` tra được
    # bằng tên hàm thay vì bằng số dòng.
    for py in root.rglob("*.py"):
        if py.stem.startswith("test_"):
            continue
        try:
            import ast as _ast

            for node in _ast.walk(_ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, _ast.FunctionDef) and py.stem in covered:
                    covered.add(node.name)
        except (SyntaxError, UnicodeDecodeError):
            continue
    # Không bao giờ để hit > total: nếu xảy ra thì mẫu số sai, và một tỉ lệ
    # trên 100% là dấu hiệu đầu tiên nhìn thấy được của chuyện đó.
    lines_total = max(lines_total, lines_hit)
    return result.exit_code, covered, (lines_hit, lines_total)
