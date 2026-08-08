"""Ghim hành vi của `core/exec/mutate.py`.

Luật trung tâm bị ghim ở đây: **"chưa chạy" không bao giờ được biến thành
"test yếu"**. DEBTS.md O1 — ba con số rỗng liên tiếp cho cùng một phép đo, và
lần nguy hiểm nhất là `0.0` đọc y hệt "oracle mù hoàn toàn" trong khi sự thật
là "lỗi cấy chưa từng chạy một dòng".
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.contracts.errors import CertusError  # noqa: E402
from app.core.exec import mutate  # noqa: E402
from app.core.exec.mutate import (  # noqa: E402
    CONCLUSIVE,
    MUTMUT_DIR,
    STATUS_BY_EXIT_CODE,
    MutationReport,
    demangle,
    function_lines,
    mutmut_argv,
    parse_mutmut_meta,
    run_mutations,
)
from app.core.exec.runner import ProbeResult, load_exec_config  # noqa: E402

SOURCE = textwrap.dedent(
    '''\
    """Module mẫu."""


    def add(a, b):
        return a + b


    def lonely(x):
        return x * 2


    class Cart:
        def total(self, items):
            return sum(items)
    '''
)

#: exit code → ý nghĩa, theo bảng của mutmut.
KILLED, SURVIVED, NO_TESTS, TIMEOUT = 1, 0, 5, 36


def _build_workspace(
    tmp_path: Path,
    *,
    exit_codes: dict[str, int],
    tests_by_function: dict[str, list[str]] | None = None,
    write_stats: bool = True,
) -> Path:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "mod.py").write_text(SOURCE, encoding="utf-8")

    mutants = workspace / "mutants" / "pkg"
    mutants.mkdir(parents=True)
    (mutants / "mod.py.meta").write_text(
        json.dumps(
            {
                "exit_code_by_key": exit_codes,
                "type_check_error_by_key": {},
                "durations_by_key": {},
                "estimated_durations_by_key": {},
            }
        ),
        encoding="utf-8",
    )
    if write_stats:
        (workspace / "mutants" / "mutmut-stats.json").write_text(
            json.dumps(
                {
                    "tests_by_mangled_function_name": tests_by_function or {},
                    "duration_by_test": {},
                    "stats_time": 0.0,
                }
            ),
            encoding="utf-8",
        )
    return workspace


# ── giải tên & tra dòng ─────────────────────────────────────────────────


def test_demangle_module_level_function() -> None:
    assert demangle("x_add__mutmut_3") == (None, "add")


def test_demangle_method() -> None:
    assert demangle("xǁCartǁtotal__mutmut_1") == ("Cart", "total")


def test_demangle_strips_module_prefix() -> None:
    """mutmut 3.2 tiền tố tên mutant bằng đường dẫn module có dấu chấm.

    Hồi quy cho lệch phiên bản ĐO được: bộ test tổng hợp trước đây chỉ dùng tên
    trần, nên `demangle` trả `(None, "")` cho MỌI mutant thật → line=0 → không
    mutant nào `bound` → cổng mutation câm lặng fail-closed. Neo phải sống sót
    qua tiền tố module.
    """
    assert demangle("payments.gateway.xǁPaymentGatewayǁcharge__mutmut_1") == (
        "PaymentGateway",
        "charge",
    )
    assert demangle("payments.config.x_load_settings__mutmut_2") == (None, "load_settings")


@pytest.mark.parametrize("name", ["add", "x_add", "", "xǁCart__mutmut_1"])
def test_demangle_refuses_to_guess(name: str) -> None:
    """Đoán sai tên hàm nghĩa là gán số dòng của hàm khác cho một mutant."""
    assert demangle(name)[1] == ""


def test_function_lines_uses_ast_not_text(tmp_path: Path) -> None:
    """Bài học B25: cấy — và neo — phải nhắm CẤU TRÚC mã, không nhắm chuỗi."""
    source = tmp_path / "mod.py"
    source.write_text(
        '"""\ndef add(a, b):  # dòng này nằm trong docstring\n    ...\n"""\n\n\ndef add(a, b):\n    return a + b\n',
        encoding="utf-8",
    )
    table = function_lines(source)
    assert table[(None, "add")] == 7


def test_function_lines_on_unparsable_file_is_empty_not_a_crash(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("def (:\n", encoding="utf-8")
    assert function_lines(source) == {}


# ── bảng trạng thái ─────────────────────────────────────────────────────


def test_status_table_matches_mutmut() -> None:
    assert STATUS_BY_EXIT_CODE[KILLED] == "killed"
    assert STATUS_BY_EXIT_CODE[SURVIVED] == "survived"
    assert STATUS_BY_EXIT_CODE[NO_TESTS] == "no tests"
    assert STATUS_BY_EXIT_CODE[TIMEOUT] == "timeout"
    assert STATUS_BY_EXIT_CODE[None] == "not checked"


def test_only_two_statuses_are_conclusive() -> None:
    assert CONCLUSIVE == {"killed", "survived"}


# ── parse ───────────────────────────────────────────────────────────────


def test_killed_and_survived_are_reported_with_source_lines(tmp_path: Path) -> None:
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_add__mutmut_1": KILLED, "x_add__mutmut_2": SURVIVED},
        tests_by_function={"x_add": ["tests/test_mod.py::test_add"]},
    )
    report = parse_mutmut_meta(workspace, seed_id="seed-1")

    assert {r.result for r in report.runs} == {"killed", "survived"}
    assert report.killed == 1
    assert report.survived == 1
    assert report.unresolved == []
    for run in report.runs:
        assert run.file == "pkg/mod.py"
        assert run.line == 4  # `def add` trong SOURCE
        assert run.seed_id == "seed-1"
        assert run.mutant_in_force is True
        assert run.bound is True


def test_method_mutants_resolve_through_the_class_separator(tmp_path: Path) -> None:
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"xǁCartǁtotal__mutmut_1": KILLED},
        tests_by_function={"xǁCartǁtotal": ["t"]},
    )
    run = parse_mutmut_meta(workspace, seed_id="s").runs[0]
    assert run.line == 13  # `def total` trong SOURCE
    assert run.bound is True


def test_no_tests_never_becomes_survived(tmp_path: Path) -> None:
    """Cốt lõi của O1: một mutant không test nào chạm KHÔNG phải một mutant sống sót."""
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_lonely__mutmut_1": NO_TESTS},
        tests_by_function={},
    )
    report = parse_mutmut_meta(workspace, seed_id="s")

    assert report.runs == []
    assert len(report.unresolved) == 1
    assert report.unresolved[0].status == "no tests"
    assert report.unresolved[0].file == "pkg/mod.py"
    assert report.unresolved[0].line == 8


def test_timeout_goes_to_unresolved_with_its_reason(tmp_path: Path) -> None:
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_add__mutmut_1": TIMEOUT},
        tests_by_function={"x_add": ["t"]},
    )
    report = parse_mutmut_meta(workspace, seed_id="s")
    assert report.runs == []
    assert report.unresolved[0].status == "timeout"


def test_unknown_exit_code_stays_unknown(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path, exit_codes={"x_add__mutmut_1": 99})
    report = parse_mutmut_meta(workspace, seed_id="s")
    assert report.unresolved[0].status == "suspicious"


def test_a_survivor_no_test_touches_is_not_in_force(tmp_path: Path) -> None:
    """exit code đẹp nhưng không test nào gọi tới hàm ⇒ phép đo không nói gì."""
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_lonely__mutmut_1": SURVIVED},
        tests_by_function={"x_add": ["t"]},
    )
    run = parse_mutmut_meta(workspace, seed_id="s").runs[0]
    assert run.result == "survived"
    assert run.tests_touching == 0
    assert run.mutant_in_force is False
    assert run.bound is False


def test_missing_stats_file_means_no_evidence_not_free_evidence(tmp_path: Path) -> None:
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_add__mutmut_1": KILLED},
        write_stats=False,
    )
    run = parse_mutmut_meta(workspace, seed_id="s").runs[0]
    assert run.mutant_in_force is False
    assert run.bound is False


def test_binding_needs_a_seed(tmp_path: Path) -> None:
    """Năm trường binding: mutant_id · seed_id · file · line · exit_code."""
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_add__mutmut_1": KILLED},
        tests_by_function={"x_add": ["t"]},
    )
    run = parse_mutmut_meta(workspace, seed_id="").runs[0]
    assert run.bound is False


def test_empty_mutants_dir_yields_an_empty_report_that_says_so(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    report = parse_mutmut_meta(workspace, seed_id="s")
    assert report.total_seen == 0
    assert report.runs == []


def test_totals_account_for_every_mutant_seen(tmp_path: Path) -> None:
    """Không mutant nào bốc hơi giữa `runs` và `unresolved`."""
    exit_codes = {
        "x_add__mutmut_1": KILLED,
        "x_add__mutmut_2": SURVIVED,
        "x_lonely__mutmut_1": NO_TESTS,
        "xǁCartǁtotal__mutmut_1": TIMEOUT,
    }
    workspace = _build_workspace(
        tmp_path, exit_codes=exit_codes, tests_by_function={"x_add": ["t"]}
    )
    report = parse_mutmut_meta(workspace, seed_id="s")
    assert report.total_seen == len(exit_codes)
    assert len(report.runs) == 2
    assert len(report.unresolved) == 2


# ── argv & wiring ───────────────────────────────────────────────────────


def test_mutmut_is_invoked_through_the_interpreter() -> None:
    """`mutmut` entry point không chắc nằm trên PATH của tiến trình con."""
    cfg = load_exec_config(reload=True)
    argv = mutmut_argv(["pkg/mod.py"], cfg)
    assert argv[:4] == ["python", "-m", "mutmut", "run"]
    assert str(cfg.mutation.max_children) in argv
    # Positional — đó là hình dạng THẬT của `mutmut run` 3.2 (`MUTANT_NAMES`).
    # Nó KHÔNG nhận cờ đường dẫn; phạm vi mutate đến từ `[mutmut] paths_to_mutate`
    # trong setup.cfg. Cửa chặn cho ca thiếu khoá đó nằm ở `run_mutations`, xem
    # `test_run_mutations_refuses_an_empty_report_from_a_failed_probe`.
    assert argv[-1] == "pkg/mod.py"


def test_run_mutations_refuses_an_empty_report_from_a_failed_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 mutant + probe hỏng = CHƯA ĐO, không phải "đo xong, sạch".

    Ca thật đã gặp: mutmut 3.2 không nhận đường dẫn qua CLI và cũng không đọc
    `paths_to_mutate` từ setup.cfg; nó đoán từ CWD, đoán trượt thì ném ngay ở
    bước sinh mutant. Probe về exit 1, không tệp `.meta` nào tồn tại, và report
    ra `killed=0, survived=0` — đọc y hệt một bộ kiểm hoàn hảo.
    """
    workspace = tmp_path / "trong"
    (workspace / MUTMUT_DIR).mkdir(parents=True)

    def fake_run_probe(ws: Path, argv: list[str], **kwargs: object) -> ProbeResult:
        return ProbeResult(
            exit_code=1,
            stdout="generating mutants",
            stderr="FileNotFoundError: Could not figure out where the code to mutate is.",
            duration_ms=1,
            command=" ".join(argv),
            evidence_id="ev-1",
        )

    monkeypatch.setattr(mutate, "run_probe", fake_run_probe)
    with pytest.raises(CertusError) as exc:
        run_mutations(workspace, seed_id="seed-1")
    assert "chưa xảy ra" in str(exc.value)


def test_run_mutations_logs_the_run_and_reads_the_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _build_workspace(
        tmp_path,
        exit_codes={"x_add__mutmut_1": KILLED},
        tests_by_function={"x_add": ["t"]},
    )
    seen: list[list[str]] = []

    def fake_run_probe(ws: Path, argv: list[str], **kwargs: object) -> ProbeResult:
        seen.append(argv)
        return ProbeResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
            command=" ".join(argv),
            evidence_id="ev-9",
        )

    monkeypatch.setattr(mutate, "run_probe", fake_run_probe)
    report: MutationReport = run_mutations(workspace, seed_id="seed-42")

    assert seen and seen[0][:3] == ["python", "-m", "mutmut"]
    assert report.evidence_id == "ev-9"
    assert report.seed_id == "seed-42"
    assert report.killed == 1
