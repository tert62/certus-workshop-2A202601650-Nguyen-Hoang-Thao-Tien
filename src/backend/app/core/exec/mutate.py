"""Chạy kiểm thử đột biến bằng `mutmut` và trả về từng mutant kèm bằng chứng.

Vì sao gọi CLI chứ không nhúng API — đã ĐO, không phải phỏng đoán
─────────────────────────────────────────────────────────────────
`mutmut` 3.6 không có API công khai: `mutmut/__init__.py` chỉ có `__getattr__`
và `_reset_globals()`, toàn bộ logic nằm trong `__main__.py` dưới `@cli.command()`,
dùng state toàn cục ở mức module và biến môi trường `MUTANT_UNDER_TEST`, và ghi
thẳng vào `./mutants/` theo cwd. Import trực tiếp hỏng thật:

    $ python3 -c "from mutmut.__main__ import status_by_exit_code"
    AttributeError: module 'threading' has no attribute 'local'

(gói ship kèm package con `mutmut/threading` che stdlib khi import ngoài luồng
CLI). Nên: gọi CLI, NHƯNG không parse chữ dành cho người đọc. `mutmut` để lại
artifact JSON có cấu trúc và ta đọc đúng chúng:

    mutants/<src>.meta            → exit_code_by_key: mutant nào ra exit code nào
    mutants/mutmut-stats.json     → tests_by_mangled_function_name: test nào chạm hàm nào
    mutants/mutmut-cicd-stats.json→ tổng hợp, chỉ để đối chiếu

Chi tiết đầy đủ: docs/design/sdd/03-core-exec.md §5.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.contracts.errors import CertusError
from app.core.exec.runner import (
    EvidenceLedger,
    ExecConfig,
    ProbeResult,
    load_exec_config,
    run_probe,
)

#: Ánh xạ exit code → trạng thái, CHÉP từ `mutmut/__main__.py:77-99` (bản 3.6).
#: Chép chứ không import, vì import chính là thứ vừa đo thấy hỏng (xem docstring
#: module). Mặc định của mutmut là "suspicious" cho mọi mã lạ — giữ nguyên, vì
#: "không biết" phải giữ nguyên là "không biết".
STATUS_BY_EXIT_CODE: dict[int | None, str] = {
    None: "not checked",
    0: "survived",
    1: "killed",
    2: "check was interrupted by user",
    3: "killed",
    5: "no tests",
    24: "timeout",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    152: "timeout",
    255: "timeout",
    -9: "segfault",
    -11: "segfault",
    -24: "timeout",
}

#: Hai trạng thái DUY NHẤT được coi là một phép đo. Mọi trạng thái khác đi vào
#: `unresolved`. Đây là ranh giới trung tâm của module: "chưa chạy" không được
#: phép biến thành "test yếu".
CONCLUSIVE = {"killed", "survived"}

#: Dấu ngăn tên lớp trong tên hàm bị mangle của mutmut
#: (`mutmut/mutation/trampoline_templates.py:1`).
CLASS_NAME_SEPARATOR = "ǁ"

MUTMUT_DIR = "mutants"
META_SUFFIX = ".meta"
STATS_FILE = "mutmut-stats.json"


@dataclass(frozen=True)
class MutationRun:
    """Một mutant đã CHẠY và cho ra kết luận.

    `mutant_in_force` là trường mà cả module này tồn tại vì nó. `DEBTS.md` mục
    O1 ghi lại ba con số rỗng liên tiếp cho cùng một phép đo, và lần thứ ba nguy
    hiểm nhất: *"0.0 đọc y hệt oracle mù hoàn toàn, sự thật là lỗi cấy chưa từng
    chạy một dòng."* Một mutation score không kèm bằng chứng mutant đã chạy là
    một con số không phân biệt được hai chuyện đó.
    """

    mutant_id: str
    seed_id: str
    result: Literal["killed", "survived"]
    bound: bool
    file: str
    line: int
    mutant_in_force: bool
    exit_code: int
    status: str
    tests_touching: int


@dataclass(frozen=True)
class UnresolvedMutant:
    """Mutant KHÔNG cho ra kết luận: no tests · timeout · skipped · segfault…

    Có tên, có lý do, và không bao giờ bị nhét vào `survived`. Cùng khuôn với
    `cells_excluded_na` của rollup: loại trừ phải NHÌN THẤY được.
    """

    mutant_id: str
    file: str
    line: int
    status: str
    exit_code: int | None


@dataclass(frozen=True)
class MutationReport:
    """Hai danh sách, không phải một.

    Trả một `list[MutationRun]` trơ sẽ buộc mọi mutant không-kết-luận-được hoặc
    biến mất (mẫu số co lại trong im lặng — lỗi B6) hoặc bị tính là `survived`
    (biến "chưa chạy" thành "test yếu" — lỗi O1). `report.runs` chính là list
    mà người gọi cần; `report.unresolved` là thứ giữ cho con số trung thực.
    """

    seed_id: str
    runs: list[MutationRun] = field(default_factory=list)
    unresolved: list[UnresolvedMutant] = field(default_factory=list)
    evidence_id: str | None = None
    probe: ProbeResult | None = None

    @property
    def total_seen(self) -> int:
        return len(self.runs) + len(self.unresolved)

    @property
    def killed(self) -> int:
        return sum(1 for r in self.runs if r.result == "killed")

    @property
    def survived(self) -> int:
        return sum(1 for r in self.runs if r.result == "survived")


# ──────────────────────────────────────────────────────────────────────────
# Giải tên mutant → hàm nguồn → số dòng
# ──────────────────────────────────────────────────────────────────────────


def demangle(mutant_name: str) -> tuple[str | None, str]:
    """`x_add__mutmut_3` → `(None, "add")`; `xǁCartǁadd__mutmut_1` → `("Cart", "add")`.

    Cũng nuốt được dạng có tiền tố module của mutmut 3.x:
    `payments.gateway.xǁCartǁadd__mutmut_1` → `("Cart", "add")`.

    Trả `(class_name, function_name)`. Tên không đúng khuôn ⇒ `(None, "")` chứ
    không đoán: đoán sai tên hàm nghĩa là gán số dòng của một hàm khác cho một
    mutant, và một neo sai còn tệ hơn không có neo.
    """
    if "__mutmut_" not in mutant_name:
        return None, ""
    # mutmut 3.2 phát tên mutant có tiền tố đường-dẫn-module bằng dấu chấm
    # (`payments.gateway.xǁCartǁadd__mutmut_1`) — ĐO được trên bản cài, không
    # phải dạng trần mà bộ test tổng hợp giả định. Phần bị mangle là đoạn sau
    # dấu chấm cuối; định danh Python không chứa `.` và dấu ngăn lớp là `ǁ`
    # (không phải `.`), nên rsplit trả nguyên tên đã trần và cắt đúng tên có
    # tiền tố. Lookup tests_touching ở `parse_mutmut_meta` vẫn dùng tên ĐẦY ĐỦ
    # có tiền tố (khớp key trong mutmut-stats.json) — chỗ này chỉ lo số dòng.
    bare = mutant_name.rsplit(".", 1)[-1]
    mangled = bare.partition("__mutmut_")[0]
    if mangled.startswith(f"x{CLASS_NAME_SEPARATOR}"):
        parts = mangled.split(CLASS_NAME_SEPARATOR)
        if len(parts) >= 3:
            return parts[1], parts[2]
        return None, ""
    if mangled.startswith("x_"):
        return None, mangled[2:]
    return None, ""


def function_lines(source_path: Path) -> dict[tuple[str | None, str], int]:
    """Bảng tra `(class, function) → lineno` dựng bằng `ast`.

    Bằng AST chứ không bằng chuỗi: bài học `B25` của tài liệu nền là một vòng
    cấy lỗi chọn dòng theo "văn bản thụt lề" nên trúng dòng nằm trong docstring
    và báo sai kết quả. Cấy — và neo — phải nhắm CẤU TRÚC mã.

    Độ mịn là MỨC HÀM, không phải mức toán tử: mutmut không phát ra vị trí toán
    tử ở dạng có cấu trúc, và moi nó ra từ diff sẽ đúng vào thứ luật nhà cấm.
    """
    table: dict[tuple[str | None, str], int] = {}
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return table

    def walk(node: ast.AST, class_name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                table.setdefault((class_name, child.name), child.lineno)
                walk(child, class_name)
            else:
                walk(child, class_name)

    walk(tree, None)
    return table


# ──────────────────────────────────────────────────────────────────────────
# Đọc artifact có cấu trúc của mutmut
# ──────────────────────────────────────────────────────────────────────────


def _read_tests_by_function(mutants_dir: Path) -> dict[str, int]:
    """`mangled function name → số test chạm tới nó`.

    Đây là nửa thứ hai của bằng chứng "mutant thực sự chạy": một mutant nằm
    trong hàm mà KHÔNG test nào gọi tới thì dù exit code có đẹp đến đâu, phép đo
    cũng không nói gì về chất lượng bộ kiểm.

    Không có file stats ⇒ trả rỗng ⇒ mọi mutant có `tests_touching == 0` ⇒ mọi
    `mutant_in_force` là False. Vắng bằng chứng đọc là "không có bằng chứng",
    không phải "có bằng chứng".
    """
    stats_path = mutants_dir / STATS_FILE
    if not stats_path.exists():
        return {}
    try:
        raw = json.loads(stats_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    by_function = raw.get("tests_by_mangled_function_name") or {}
    return {name: len(tests) for name, tests in by_function.items()}


def _meta_files(mutants_dir: Path) -> list[Path]:
    return sorted(mutants_dir.rglob(f"*{META_SUFFIX}"))


def parse_mutmut_meta(
    workspace: Path,
    *,
    seed_id: str,
    evidence_id: str | None = None,
    probe: ProbeResult | None = None,
) -> MutationReport:
    """Dựng MutationReport từ `mutants/` mà `mutmut run` để lại.

    Tách khỏi `run_mutations()` để phần suy diễn kiểm được mà không cần chạy
    mutmut thật — và để lượt chạy thật với lượt đọc lại đi qua đúng một đường.
    """
    workspace = Path(workspace)
    mutants_dir = workspace / MUTMUT_DIR
    tests_by_function = _read_tests_by_function(mutants_dir)

    runs: list[MutationRun] = []
    unresolved: list[UnresolvedMutant] = []

    for meta_path in _meta_files(mutants_dir):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        exit_code_by_key = meta.get("exit_code_by_key") or {}

        # mutants/pkg/mod.py.meta → pkg/mod.py
        rel_source = meta_path.relative_to(mutants_dir).as_posix()
        rel_source = rel_source[: -len(META_SUFFIX)]
        line_table = function_lines(workspace / rel_source)

        for mutant_name, exit_code in sorted(exit_code_by_key.items()):
            class_name, func_name = demangle(mutant_name)
            line = line_table.get((class_name, func_name), 0)
            mutant_id = f"{rel_source}::{mutant_name}"
            status = STATUS_BY_EXIT_CODE.get(exit_code, "suspicious")

            mangled = mutant_name.partition("__mutmut_")[0]
            touching = tests_by_function.get(mangled, 0)

            if status not in CONCLUSIVE:
                unresolved.append(
                    UnresolvedMutant(
                        mutant_id=mutant_id,
                        file=rel_source,
                        line=line,
                        status=status,
                        exit_code=exit_code,
                    )
                )
                continue

            in_force = touching > 0
            # Binding đủ 5 trường (note 02 §3.1: thiếu ⇒ mutation_unbound):
            # mutant_id · seed_id · file · line · exit_code. Cộng thêm điều kiện
            # mutant đã thật sự chạy, vì một binding đầy đủ trỏ vào một lượt
            # chạy chưa từng xảy ra vẫn là một binding rỗng.
            bound = bool(mutant_id and seed_id and rel_source and line > 0) and in_force

            runs.append(
                MutationRun(
                    mutant_id=mutant_id,
                    seed_id=seed_id,
                    result="killed" if status == "killed" else "survived",
                    bound=bound,
                    file=rel_source,
                    line=line,
                    mutant_in_force=in_force,
                    exit_code=int(exit_code),
                    status=status,
                    tests_touching=touching,
                )
            )

    return MutationReport(
        seed_id=seed_id,
        runs=runs,
        unresolved=unresolved,
        evidence_id=evidence_id,
        probe=probe,
    )


def mutmut_argv(paths: list[str] | None, cfg: ExecConfig) -> list[str]:
    """Dựng argv cho lượt mutmut.

    Gọi `python -m mutmut` chứ không gọi `mutmut`: entry point script không chắc
    nằm trên PATH của tiến trình con (venv, pipx, cài user-level), còn interpreter
    thì luôn có.

    CẢNH BÁO VỀ `paths` — đã đọc mã mutmut 3.2.0 và chạy thử, không phải suy đoán.

    `mutmut run` chỉ nhận `--max-children` và một danh sách positional
    `MUTANT_NAMES`. Nó KHÔNG có cờ đường dẫn nào: `--paths-to-mutate` cho ra
    `Error: No such option`. Và `[mutmut] paths_to_mutate` trong `setup.cfg`
    cũng KHÔNG được đọc — `read_config()` (`__main__.py:948`) chỉ lấy
    `do_not_mutate` và `also_copy`, còn dòng 152 gọi thẳng
    `guess_paths_to_mutate()` vô điều kiện. Thông báo lỗi của chính mutmut chỉ
    tới hai cách sửa mà bản 3.2 không cài đặt cái nào.

    Phạm vi mutate vì vậy do CWD quyết định: `guess_paths_to_mutate()` lấy
    `lib/`, rồi `src/`, rồi một thư mục TRÙNG TÊN với thư mục hiện hành. Nên
    workspace phải là repo có layout `<tên>/<tên>/` — đúng layout của
    `fixtures/targets/*`, và đó là lý do nó chạy được ở đó mà không chạy được
    trên một bản sao đặt tên khác.

    Hệ quả nếu không ai canh: probe exit 1, không tệp `.meta` nào được sinh,
    `parse_mutmut_meta` trả 0 killed / 0 survived / 0 seen — một lượt CHƯA TỪNG
    CHẠY đọc y hệt một lượt bộ kiểm giết sạch. Đó đúng là `O1` của DEBTS.md xảy
    ra bên trong chính module viết ra để chống nó. Cửa chặn nằm ở
    `run_mutations()` bên dưới.
    """
    argv = ["python", "-m", "mutmut", "run", "--max-children", str(cfg.mutation.max_children)]
    if paths:
        argv.extend(paths)
    return argv


def run_mutations(
    workspace: Path,
    *,
    seed_id: str,
    paths: list[str] | None = None,
    ledger: EvidenceLedger | None = None,
    config: ExecConfig | None = None,
    claim_id: str = "mutation",
) -> MutationReport:
    """Chạy `mutmut` trong sandbox rồi đọc lại artifact của nó.

    Đi qua `run_probe()` để lượt mutation cũng để lại record trong sổ bằng chứng
    như mọi lượt chạy khác — mutation là một lần thi hành mã người dùng, không
    phải một phép tính.

    Lượt chạy bị chặn hoặc hết giờ KHÔNG trả report rỗng đọc như "0 mutant sống":
    artifact vẫn được đọc, và những gì mutmut chưa kịp kết luận nằm nguyên trong
    `unresolved`.
    """
    cfg = config or load_exec_config()
    probe = run_probe(
        Path(workspace),
        mutmut_argv(paths, cfg),
        ledger=ledger,
        claim_id=claim_id,
        timeout_s=cfg.mutation.timeout_seconds,
        config=cfg,
    )
    report = parse_mutmut_meta(
        workspace,
        seed_id=seed_id,
        evidence_id=probe.evidence_id,
        probe=probe,
    )
    # Fail-closed: lượt chạy hỏng mà KHÔNG thấy mutant nào là "chưa đo", không
    # phải "đo xong, sạch". Hai thứ đó cùng cho `killed=0, survived=0` nên chúng
    # phải được tách ở đây, chỗ duy nhất còn cầm cả exit code lẫn report.
    # Nguyên nhân hay gặp nhất: mutmut không đoán ra thư mục mã nguồn, vì nó chỉ
    # nhận `lib/`, `src/`, hoặc một thư mục trùng tên với CWD (xem `mutmut_argv`).
    if report.total_seen == 0 and (probe.exit_code != 0 or probe.blocked):
        raise CertusError(
            f"lượt mutation không sinh ra mutant nào và probe kết thúc bất thường "
            f"(exit {probe.exit_code}, blocked={probe.blocked}). Đây KHÔNG phải "
            f"mutation score 0 — đây là lượt chạy chưa xảy ra. mutmut 3.2 tìm mã "
            f"nguồn bằng cách đoán từ CWD ({workspace}): nó chỉ nhận `lib/`, "
            f"`src/`, hoặc một thư mục trùng tên thư mục hiện hành. "
            f"stderr: {probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else '(rỗng)'}"
        )
    return report


__all__ = [
    "CONCLUSIVE",
    "STATUS_BY_EXIT_CODE",
    "MutationReport",
    "MutationRun",
    "UnresolvedMutant",
    "demangle",
    "function_lines",
    "mutmut_argv",
    "parse_mutmut_meta",
    "run_mutations",
]
