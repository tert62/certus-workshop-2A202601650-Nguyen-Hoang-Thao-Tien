"""Golden eval của CERTUS — `python -m certus evals`.

Tệp này tồn tại vì `certus/__main__.py` đã khai lệnh `evals` và gọi thẳng
`evals/run.py`, trong khi thư mục `evals/` chưa từng tồn tại: lệnh chạy ra
`can't open file ... evals/run.py` và trả về mã lỗi của interpreter. Một lệnh
có trong `--help` mà không chạy được là một lời hứa mà bộ kiểm không giữ.

Ba phép kiểm, cố ý KHÔNG gộp thành một điểm số:

1. **Cassette** — mọi claim trong `fixtures/cassettes/*.json` có qua nổi
   validator của chính hệ không. Đây là chỗ hở đã đo được: 9/10 cassette chứa
   3–6 claim `OBSERVED` không mang anchor, và pipeline loại chúng lúc chạy nên
   sinh viên nhận một câu trả lời ngắn hơn bản đã thu. Kiểm ở đây biến một
   khuyết tật fixture im lặng thành một dòng đỏ.

2. **Golden số học** — chạy thật 3 repo mẫu và so từng con số với
   `evals/golden.json`. Line/mutation/grid đứng RIÊNG, không có trung bình nào:
   một con số tụt mà hai con số kia bù lại vẫn phải là ĐỎ.

3. **Bất biến hợp đồng** — những thứ không được phép đúng "gần đúng": mẫu số 0
   không bao giờ ra tỉ lệ, mọi tỉ lệ đều mang interval, ô `unknown` không bị
   đếm là đã phủ.

Chạy: `python -m certus evals` · cập nhật golden: `python evals/run.py --update`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.json"

#: Khoá của khối cassette trong golden. Tiền tố `_` để nó không lẫn với tên repo
#: mẫu — vòng lặp so sánh repo duyệt theo `TARGETS`, không duyệt theo khoá file.
_CASSETTE_KEY = "_cassettes"

#: Repo mẫu + câu hỏi. Câu hỏi là một PHẦN của khoá cassette, nên nó phải trùng
#: từng ký tự với câu mặc định của CLI — đổi ở đây mà không đổi ở kia là tự tạo
#: ra một lượt chạy trượt cassette rồi kết luận "cassette hỏng".
TARGETS: tuple[tuple[str, str], ...] = (
    ("shopcart", "Bộ kiểm thử của repo này phủ tới đâu?"),
    ("ledger", "Bộ kiểm thử của repo này phủ tới đâu?"),
    ("payments", "Bộ kiểm thử của repo này phủ tới đâu?"),
)


def _fail(msg: str) -> str:
    return f"  ✗ {msg}"


def _ok(msg: str) -> str:
    return f"  ✓ {msg}"


# ── 1. cassette ────────────────────────────────────────────────────────────


def cassette_rejections() -> dict[str, int | str]:
    """{tên cassette: số claim bị validator từ chối} — hoặc chuỗi lỗi nếu không đọc được.

    KHÔNG tự sửa cassette, và đây là một quyết định chứ không phải sự lười:

    * Thêm `anchors` thay cho mô hình là bịa ra bằng chứng. Hệ nhãn của cả sản
      phẩm này tồn tại để chặn đúng động tác đó, nên làm nó trong `fixtures/` để
      bảng eval xanh lên là tự phản bội.
    * Bản ghi mang `recorded_at` và có nghĩa là "mô hình đã nói đúng thế này".
      Sửa nội dung làm nó thôi là một bản ghi.

    Cách sửa thật: siết `prompts/analyze.md` đòi anchor RỒI thu lại
    (`CERTUS_LLM_MODE=record`) trong CÙNG một lần — prompt nằm trong khoá
    cassette nên đổi prompt mà không thu lại là làm cả lớp trượt cassette.

    Trong lúc chưa thu lại: con số bị từ chối được GHIM vào golden. Nó không
    biến mất, nó trở thành một đại lượng có người canh — đúng thứ luồng chạy
    hiện đã phơi ra bằng `AnalyzeResponse.rejected_claims`.
    """
    from app.agent.claims import extract_claims_json, parse_claims
    from app.contracts.types import Claim

    out: dict[str, int | str] = {}
    for path in sorted((REPO_ROOT / "fixtures" / "cassettes").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        text = record.get("response", {}).get("text", "")
        try:
            claims = parse_claims(extract_claims_json(text))
        except Exception as exc:  # noqa: BLE001 — mọi kiểu hỏng đều phải hiện ra
            # Cassette chat không mang mảng `claims`; đó là hình dạng hợp lệ của
            # nó, không phải lỗi. Phân biệt bằng tiền tố tên tệp chứ không bằng
            # việc nuốt mọi ngoại lệ.
            out[path.name] = 0 if path.name.startswith("chat__") else f"không đọc được: {exc}"
            continue

        rejected = 0
        for claim in claims:
            try:
                Claim.model_validate(claim.model_dump())
            except Exception:  # noqa: BLE001, S110 — chi tiết đã có ở luồng chạy thật
                rejected += 1
        out[path.name] = rejected
    return out


def check_cassettes(golden: dict[str, Any] | None) -> tuple[list[str], int]:
    """So số claim bị từ chối với con số đã ghim trong golden."""
    measured = cassette_rejections()
    if not measured:
        return [_fail("không có cassette nào trong fixtures/cassettes")], 1

    expected: dict[str, Any] = (golden or {}).get(_CASSETTE_KEY, {})
    lines: list[str] = []
    failures = 0
    known_bad = 0

    for name in sorted(set(measured) | set(expected)):
        got = measured.get(name, "VẮNG MẶT")
        want = expected.get(name, "CHƯA GHIM")
        if got != want:
            lines.append(_fail(f"{name}: golden {want} → đo được {got}"))
            failures += 1
        elif isinstance(got, int) and got > 0:
            known_bad += got
            lines.append(_ok(f"{name}: {got} claim bị từ chối, đúng bằng con số đã ghim"))
        else:
            lines.append(_ok(f"{name}: không claim nào bị từ chối"))

    if known_bad:
        lines.append(
            f"  ! {known_bad} claim `OBSERVED` không mang anchor trên toàn bộ cassette. "
            f"Đây là NỢ ĐÃ GHIM, không phải xanh: sửa bằng cách siết prompt và thu lại "
            f"cassette trong cùng một lần."
        )
    return lines, failures


# ── 2. golden số học ───────────────────────────────────────────────────────


def _rate_tuple(rate: Any) -> list[int] | None:
    return None if rate is None else [rate.k, rate.n]


def measure(target: str, question: str) -> dict[str, Any]:
    """Chạy thật một repo mẫu và rút ra những con số phải bất biến."""
    from app.api.schemas import AnalyzeRequest
    from app.orchestrator.pipeline import analyze

    result = asyncio.run(analyze(AnalyzeRequest(target=target, question=question)))
    resp = result.response
    cov = resp.coverage
    return {
        "line": _rate_tuple(cov.line),
        "mutation": _rate_tuple(cov.mutation),
        "grid": _rate_tuple(cov.grid),
        "cells_total": cov.cells_total,
        "cells_na": cov.cells_na,
        "cells_unknown": cov.cells_unknown,
        "zones": len(cov.per_zone),
        "gates": len(resp.gates),
        "claims": len(resp.claims),
        "rejected_claims": len(resp.rejected_claims),
        "verdict": resp.verdict,
    }


def load_golden() -> dict[str, Any] | None:
    if not GOLDEN_PATH.is_file():
        return None
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def write_golden() -> list[str]:
    payload: dict[str, Any] = {name: measure(name, q) for name, q in TARGETS}
    payload[_CASSETTE_KEY] = cassette_rejections()
    GOLDEN_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return [_ok(f"đã ghi {GOLDEN_PATH.relative_to(REPO_ROOT)}")]


def check_golden(golden: dict[str, Any] | None) -> tuple[list[str], int]:
    if golden is None:
        return [
            _fail(
                f"chưa có {GOLDEN_PATH.name}. Sinh lần đầu bằng "
                f"`python evals/run.py --update` rồi commit nó."
            )
        ], 1

    measured = {name: measure(name, q) for name, q in TARGETS}
    lines: list[str] = []
    failures = 0
    for name, actual in measured.items():
        expected = golden.get(name)
        if expected is None:
            lines.append(_fail(f"{name}: chưa có trong golden"))
            failures += 1
            continue
        drift = {
            key: (expected.get(key), value)
            for key, value in actual.items()
            if expected.get(key) != value
        }
        if drift:
            for key, (want, got) in drift.items():
                lines.append(_fail(f"{name}.{key}: golden {want} → đo được {got}"))
            failures += 1
        else:
            lines.append(_ok(f"{name}: {len(actual)} chỉ số khớp golden"))
    return lines, failures


# ── 3. bất biến hợp đồng ───────────────────────────────────────────────────


def check_invariants() -> tuple[list[str], int]:
    """Những thứ không được phép "gần đúng"."""
    from app.contracts.errors import CertusError
    from app.orchestrator.pipeline import rate

    lines: list[str] = []
    failures = 0

    try:
        rate("thử", 0, 0)
    except CertusError:
        lines.append(_ok("mẫu số 0 không sinh ra tỉ lệ nào"))
    else:
        lines.append(_fail("rate() chấp nhận mẫu số 0 — một tỉ lệ không có mẫu số"))
        failures += 1

    sample = rate("thử", 3, 3)
    if sample.interval is None:
        lines.append(_fail("tỉ lệ ra đời mà không có interval"))
        failures += 1
    elif sample.interval.lower >= 0.99:
        lines.append(
            _fail(f"3/3 cho biên dưới {sample.interval.lower:.3f} — quá cao, kiểm lại Wilson")
        )
        failures += 1
    else:
        lines.append(_ok(f"3/3 ⇒ biên dưới {sample.interval.lower:.3f}, không phải 1.0"))

    return lines, failures


# ── driver ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="certus evals", description="Golden eval của CERTUS")
    ap.add_argument(
        "--update", action="store_true", help="ghi lại evals/golden.json từ lượt đo này"
    )
    args = ap.parse_args(argv)

    print("\nCERTUS · golden eval")

    if args.update:
        print("\n".join(write_golden()))
        return 0

    golden = load_golden()
    total = 0

    print("\n[1/3] cassette — số claim bị từ chối có đúng con số đã ghim không")
    lines, failures = check_cassettes(golden)
    print("\n".join(lines))
    total += failures

    print("\n[2/3] golden — con số của 3 repo mẫu")
    lines, failures = check_golden(golden)
    print("\n".join(lines))
    total += failures

    print("\n[3/3] bất biến hợp đồng")
    lines, failures = check_invariants()
    print("\n".join(lines))
    total += failures

    print()
    if total:
        print(f"{total} nhóm kiểm ĐỎ. Không có điểm số trung bình nào ở đây để làm dịu nó.")
        return 1
    print("Tất cả xanh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
