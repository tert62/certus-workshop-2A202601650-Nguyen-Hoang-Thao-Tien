"""Ghim lớp lỗi "XANH CÂM" — con số 0 sinh ra từ một phép đo CHƯA XẢY RA.

Đây là lớp lỗi mà `docs/research-notes/02-tot-grid-coverage.md` §8.4 gọi là lớn
nhất, và nó đã xảy ra ba lần bên trong chính CERTUS:

1. `run_probe` tra `python`/`coverage` trên PATH. Không activate venv ⇒ probe bị
   chặn ⇒ pipeline trả `(0, 0)` ⇒ mất hẳn dòng line coverage và grid tụt
   42.9% → 0.0%, KHÔNG một cảnh báo nào.
2. `run_target_suite` bọc phần đọc coverage trong `except Exception: lines = 0`.
3. `mutmut_argv` truyền đường dẫn theo kiểu mutmut 3.2 không nhận ⇒ 0 mutant
   sinh ra ⇒ report `killed=0, survived=0`, đọc y hệt "bộ kiểm giết sạch".

Ba cái đều cho ra một con số hợp lệ về mặt kiểu dữ liệu. Bộ test cũ xanh suốt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.contracts.errors import CertusError  # noqa: E402
from app.core.exec import runner  # noqa: E402
from app.core.exec.runner import ProbeResult, load_exec_config, run_probe  # noqa: E402
from app.orchestrator import pipeline  # noqa: E402


class FakeLedger:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def append(self, **kwargs: object) -> str:
        self.records.append(kwargs)
        return f"ev-{len(self.records)}"


# ── 1. probe không còn phụ thuộc PATH ──────────────────────────────────────


def test_program_names_resolve_to_the_running_interpreter() -> None:
    """`python` phải trỏ vào interpreter ĐANG CHẠY, không phải vào PATH."""
    assert runner._resolve_program(["python", "-c", "pass"]) == [sys.executable, "-c", "pass"]


@pytest.mark.parametrize("tool", ["pytest", "coverage"])
def test_tools_run_as_modules_of_the_running_interpreter(tool: str) -> None:
    """`.venv/bin/pytest` có thể không tồn tại; `-m pytest` thì luôn có."""
    assert runner._resolve_program([tool, "-q"]) == [sys.executable, "-m", tool, "-q"]


def test_a_program_outside_the_map_is_left_untouched() -> None:
    """Chỉ ba cái tên được đổi. Đổi bừa là im lặng chạy một chương trình khác."""
    assert runner._resolve_program(["rm", "-rf", "/"]) == ["rm", "-rf", "/"]


def test_the_allowlist_still_runs_before_resolution(tmp_path: Path) -> None:
    """Thứ tự load-bearing: `sys.executable` tên `python3.12`, KHÔNG thuộc allowlist.

    Đổi tên trước rồi mới kiểm allowlist thì mọi probe hợp lệ đều bị chặn.
    """
    ledger = FakeLedger()
    result = run_probe(tmp_path, ["python", "-c", "pass"], ledger=ledger, config=load_exec_config())
    assert result.blocked is False
    assert result.exit_code == 0


def test_a_probe_runs_without_the_venv_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ca thật đã đo: chạy `.venv/bin/python -m certus` mà không activate venv."""
    monkeypatch.setenv("PATH", "/nonexistent")
    ledger = FakeLedger()
    result = run_probe(
        tmp_path, ["python", "-c", "print(7)"], ledger=ledger, config=load_exec_config()
    )
    assert result.blocked is False
    assert "7" in result.stdout


# ── 2. pipeline từ chối phát biểu trên một lượt chạy chưa xảy ra ───────────


def _blocked_probe(*_args: object, **_kwargs: object) -> ProbeResult:
    return ProbeResult(
        exit_code=126,
        stdout="",
        stderr="[Errno 2] No such file or directory: 'coverage'",
        duration_ms=1,
        blocked=True,
        block_reason="không tìm thấy chương trình",
        command="coverage run -m pytest -q",
        evidence_id="ev-1",
    )


def test_a_blocked_probe_refuses_instead_of_reporting_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.exec.runner.run_probe", _blocked_probe)
    with pytest.raises(CertusError) as exc:
        pipeline.run_target_suite(tmp_path)
    assert "không chạy được bộ kiểm" in str(exc.value)


def test_a_run_without_a_coverage_artifact_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code đẹp mà không có `.coverage` vẫn là "chưa đo được gì"."""

    def clean_probe(*_args: object, **_kwargs: object) -> ProbeResult:
        return ProbeResult(
            exit_code=0, stdout="", stderr="", duration_ms=1, command="coverage run", evidence_id="ev-1"
        )

    monkeypatch.setattr("app.core.exec.runner.run_probe", clean_probe)
    with pytest.raises(CertusError) as exc:
        pipeline.run_target_suite(tmp_path)
    assert ".coverage" in str(exc.value)


# ── 3. mẫu số thứ ba ───────────────────────────────────────────────────────


def test_mutation_counts_reads_a_rich_artifact() -> None:
    from app.orchestrator.observe import mutation_counts

    assert mutation_counts({"killed": 7, "total": 10, "verdict": "survived"}) == (7, 10)


@pytest.mark.parametrize(
    ("verdict", "expected"), [("killed", (1, 1)), ("survived", (0, 1))]
)
def test_mutation_counts_falls_back_to_the_single_mutant_form(
    verdict: str, expected: tuple[int, int]
) -> None:
    from app.orchestrator.observe import mutation_counts

    assert mutation_counts({"verdict": verdict}) == expected


def test_no_artifact_is_none_not_zero() -> None:
    """Thiếu bằng chứng ≠ bằng chứng xấu. None hiện ra là "không có dòng"."""
    from app.orchestrator.observe import mutation_counts

    assert mutation_counts(None) is None
    assert mutation_counts({}) is None


def test_a_single_mutant_carries_its_own_warning() -> None:
    """1/1 = 100% phải mang cờ `n-too-small` — bài học M1 của DEBTS.md."""
    out = pipeline.rate("mutation_score", 1, 1)
    assert out.point == 1.0
    assert "n-too-small" in out.flags
    assert out.interval.lower < 0.5, "biên dưới của 1/1 không được đọc như một sự chắc chắn"


# ── 4. neo code_path: một default không ai khai ────────────────────────────


def test_the_entry_symbol_lookup_always_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """GHIM MỘT KHUYẾT TẬT ĐANG CÓ THẬT, không phải khẳng định một hành vi đúng.

    `pipeline` lấy `code_path` bằng `self.cfg.__dict__.get("entry_symbol",
    "checkout")`. `Settings` KHÔNG có trường `entry_symbol` nào — không ở
    `settings.py`, không ở `config/*.yaml` — nên nhánh default luôn thắng và mọi
    repo đều được neo vào symbol `checkout` của riêng shopcart.

    Hệ quả đo được trên repo mẫu `ledger`: 3/6 ô CÓ quan sát (assert_count 1–2)
    nhưng `cov_cell` rỗng vì `checkout` không tồn tại trong repo đó, nên cả 6 ô
    rơi về `unknown` và grid coverage ra 0/6 — trong khi line coverage là 100%.

    Đây đúng hình dạng mà research note 02 §8.9 mô tả: *"Cấu hình sai ⇒ grid sập
    về `unknown` trong im lặng"*. Nó có thể là lỗi cài sẵn cho buổi học (`kb/README.md`
    có ghi nhận vài chỗ như vậy) hoặc là một khuyết tật thật. Test này không phán
    xử điều đó — nó chỉ làm cho việc SỬA trở nên có ý thức: đổi hành vi sẽ đổi
    con số của `ledger`, tức đổi prompt, tức phải thu lại cassette của `ledger`
    trong cùng một lần.
    """
    from app.settings import Settings

    assert "entry_symbol" not in Settings.model_fields, (
        "đã có trường entry_symbol — cập nhật `_run_inner` để đọc nó thật sự, "
        "rồi thu lại cassette của mọi repo mẫu bị đổi số"
    )
    source = (_BACKEND / "app" / "orchestrator" / "pipeline.py").read_text(encoding="utf-8")
    assert 'get("entry_symbol", "checkout")' in source


def test_the_prompt_block_stays_a_two_denominator_contract() -> None:
    """Khối prompt là hợp đồng bất biến của cassette.

    `coverage.mutation` đi vào RESPONSE, không đi vào prompt: thêm một dòng ở đó
    đổi `cassette_key` của cả 10 cassette đang commit. Ai muốn thêm thì phải thu
    lại cassette trong cùng một lần — và sẽ thấy test này đỏ trước.
    """
    source = (_BACKEND / "app" / "orchestrator" / "pipeline.py").read_text(encoding="utf-8")
    block = source.split("def _format_artifacts")[1].split("def rate(")[0]
    assert "Mutation" not in block, (
        "thêm mutation vào khối prompt làm mọi cassette trượt — thu lại cassette "
        "trong cùng một lần rồi sửa test này"
    )
