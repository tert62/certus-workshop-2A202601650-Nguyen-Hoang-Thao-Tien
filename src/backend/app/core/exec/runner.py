"""Chạy một lệnh probe trong sandbox và ghi lại bằng chứng.

Đây là chỗ DUY NHẤT trong CERTUS thực sự thi hành mã của người lạ. Mọi module
phía trên chỉ đọc lại artifact mà module này sinh ra, nên mọi thứ ở đây phải
để lại dấu vết: một lượt chạy không có record trong sổ bằng chứng đọc y hệt một
lượt chưa từng chạy, và hai thứ đó cần hai cách xử lý khác nhau.

Giới hạn khai thẳng, không giấu trong docstring dài: chế độ `subprocess` chạy
CÙNG UID với người dùng. Nó là *tamper-evident*, không phải *tamper-proof* —
xem `app/settings.py` và `docs/design/sdd/03-core-exec.md` §8.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from app.contracts.errors import CertusError, ConfigError, SandboxViolation
from app.settings import settings

# ──────────────────────────────────────────────────────────────────────────
# Cấu hình
# ──────────────────────────────────────────────────────────────────────────


class _Strict(BaseModel):
    """Khoá thừa trong yaml là LỖI, không phải khoá bị lờ đi.

    Một khoá gõ sai mà bị bỏ qua im lặng nghĩa là ngưỡng thật vẫn là default cũ
    trong khi người sửa tin rằng mình đã đổi nó.
    """

    model_config = ConfigDict(extra="forbid")


class DockerLimits(_Strict):
    mem_limit: str
    pids_limit: int
    tmpfs_size_bytes: int


class RunnerConfig(_Strict):
    env_passthrough: list[str]
    max_output_bytes: int
    docker: DockerLimits


class CoverageConfig(_Strict):
    min_files_in_report: int


class MutationConfig(_Strict):
    timeout_seconds: int
    max_children: int
    sample_rate: float


class CalibrationConfig(_Strict):
    revive_fraction: float
    conf: float
    interval_method: str
    min_sample_size: int
    false_high_block: float


class ExecConfig(_Strict):
    """Không field nào có default.

    Luật ba vế: một default trong code là một ngưỡng mất vế "suy ra từ đâu" và
    vế "điều kiện xem lại". Thiếu khoá thì phải dừng và nêu đích danh tên khoá.
    """

    runner: RunnerConfig
    coverage: CoverageConfig
    mutation: MutationConfig
    calibration: CalibrationConfig


_CONFIG_CACHE: dict[Path, ExecConfig] = {}


def exec_config_path() -> Path:
    return settings.config_dir / "exec.yaml"


def load_exec_config(path: Path | None = None, *, reload: bool = False) -> ExecConfig:
    """Đọc `config/exec.yaml`.

    Cache theo đường dẫn vì file này bị đọc ở mỗi lượt chạy probe; `reload=True`
    dành cho test và cho lệnh admin đổi config lúc chạy.
    """
    target = Path(path) if path is not None else exec_config_path()
    if not reload and target in _CONFIG_CACHE:
        return _CONFIG_CACHE[target]

    if not target.exists():
        raise ConfigError("exec.yaml", f"không tìm thấy tệp cấu hình tại {target}")

    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("exec.yaml", "nội dung không phải một mapping")

    try:
        cfg = ExecConfig.model_validate(raw)
    except ValidationError as exc:
        # Nêu ĐÍCH DANH khoá đầu tiên hỏng. "cấu hình không hợp lệ" trơ trọi
        # buộc người sửa phải đi dò, và đi dò là lúc người ta đoán.
        first = exc.errors()[0]
        key = ".".join(str(p) for p in first["loc"])
        raise ConfigError(key, first["msg"]) from exc

    _CONFIG_CACHE[target] = cfg
    return cfg


# ──────────────────────────────────────────────────────────────────────────
# Sổ bằng chứng — Protocol, không tự viết
# ──────────────────────────────────────────────────────────────────────────


class EvidenceLedger(Protocol):
    """Hợp đồng tối thiểu mà runner cần ở sổ bằng chứng.

    Sổ thật thuộc `app/ledger/evidence.py` (SDD 06) — nó lo `prev_hash` và
    `self_hash`. Runner chỉ cần biết cách nộp 5 trường và nhận lại evidence_id.
    Khai Protocol thay vì import cứng để hai module build song song được, và để
    test tiêm được sổ giả mà không đụng vào sổ thật.
    """

    def append(
        self,
        *,
        claim_id: str,
        command: str,
        exit_code: int,
        output_sha256: str,
        verdict: str,
    ) -> str: ...


def _resolve_ledger() -> EvidenceLedger:
    """Tìm sổ thật một lần.

    Không tìm thấy thì RAISE. Cố tình không có nhánh "chạy mà không ghi": nếu
    có, thì mọi lượt chạy trên máy chưa cài ledger sẽ biến mất khỏi lịch sử mà
    không ai thấy — đúng lớp lỗi "xanh câm" mà cả tài liệu nền lẫn sản phẩm này
    tồn tại để chống.
    """
    try:
        from app.ledger import evidence as evidence_module
    except ImportError as exc:  # pragma: no cover - phụ thuộc tiến độ SDD 06
        raise CertusError(
            "không có sổ bằng chứng: app.ledger.evidence chưa tồn tại. "
            "run_probe() từ chối chạy lệnh mà không ghi được record."
        ) from exc

    getter = getattr(evidence_module, "get_ledger", None)
    if callable(getter):
        return getter()
    if hasattr(evidence_module, "append"):
        return evidence_module  # type: ignore[return-value]
    raise CertusError(
        "app.ledger.evidence không cung cấp get_ledger() lẫn append(); "
        "runner không có cách ghi bằng chứng nên từ chối chạy."
    )


# ──────────────────────────────────────────────────────────────────────────
# Kết quả một lượt chạy
# ──────────────────────────────────────────────────────────────────────────

VERDICT_PASS = "executed-pass"
VERDICT_FAIL = "executed-fail"
VERDICT_UNVERIFIED = "UNVERIFIED"

#: Exit code quy ước khi lệnh không bao giờ được thi hành. 126 là quy ước POSIX
#: cho "found but not executable" — gần nghĩa nhất với "bị allowlist từ chối".
EXIT_BLOCKED = 126
#: Quy ước của `timeout(1)`.
EXIT_TIMEOUT = 124


@dataclass(frozen=True)
class ProbeResult:
    """Kết quả một lượt chạy probe.

    `blocked` nghĩa là SANDBOX đã can thiệp, không phải "test hỏng". Phân biệt
    này load-bearing: một lượt bị cắt vì hết giờ không phải một phép đo, và gộp
    nó chung với "test fail" biến một sự cố hạ tầng thành một kết luận về code
    người dùng.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    blocked: bool = False
    block_reason: str | None = None
    command: str = ""
    evidence_id: str | None = None

    @property
    def passed(self) -> bool:
        return not self.blocked and self.exit_code == 0


# ──────────────────────────────────────────────────────────────────────────
# Allowlist
# ──────────────────────────────────────────────────────────────────────────

#: Chương trình được phép chạy trong sandbox.
#:
#: Danh sách này KHÔNG nằm trong exec.yaml vì nó không phải một ngưỡng: nó không
#: có đơn vị, không có độ nhạy, không có "giá trị khởi điểm minh hoạ". Luật ba
#: vế của SDD 00 §4 nói về ngưỡng.
ALLOWED_COMMANDS = {"pytest", "coverage", "python"}


def _is_allowed(argv: list[str]) -> bool:
    """Lệnh có nằm trong allowlist không.

    Lấy `Path(...).name` để `/usr/bin/pytest` và `pytest` cho cùng kết quả —
    người dùng gọi bằng đường dẫn tuyệt đối hay tương đối không đổi phán quyết.
    """
    if not argv:
        return False
    return Path(argv[0]).name in ALLOWED_COMMANDS


# ──────────────────────────────────────────────────────────────────────────
# Thi hành
# ──────────────────────────────────────────────────────────────────────────


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    if len(text.encode("utf-8", "replace")) <= limit:
        return text
    return text.encode("utf-8", "replace")[:limit].decode("utf-8", "replace")


def _child_env(cfg: ExecConfig, run_dir: Path) -> dict[str, str]:
    """Dựng môi trường cho tiến trình con.

    Chỉ những biến khai trong `runner.env_passthrough` được đi xuyên — mặc định
    là allowlist chứ không phải blocklist, vì blocklist luôn thiếu một cái tên.
    Bốn biến còn lại do runner tự đặt và trỏ vào thư mục tạm của lượt chạy, để
    tiến trình con không rải `__pycache__` vào cây mã người dùng và để hai lượt
    chạy cùng đầu vào cho cùng kết quả (PYTHONHASHSEED).
    """
    env = {name: os.environ[name] for name in cfg.runner.env_passthrough if name in os.environ}
    env.setdefault("PATH", os.defpath)
    env["TMPDIR"] = str(run_dir)
    env["PYTHONPYCACHEPREFIX"] = str(run_dir / "pycache")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    return env


#: Tên logic được nối thẳng vào interpreter đang chạy thay vì tra PATH.
_INTERPRETER_NAMES = {"python", "python3"}
_INTERPRETER_MODULES = {"pytest", "coverage"}


def _resolve_program(argv: list[str]) -> list[str]:
    """Đổi tên chương trình logic thành interpreter ĐANG CHẠY.

    Vì sao không để `subprocess` tra PATH: `python`, `pytest`, `coverage` chỉ có
    trên PATH khi venv đã được activate. Ai chạy `.venv/bin/python -m certus`
    (đúng như docs/setup.md gợi ý ở Bước 6, và đúng như IDE hay làm) thì con
    không tìm thấy `coverage` → probe bị chặn → mẫu số về 0. Đo thật: cùng một
    repo cho `line 156/160 · grid 27/63` khi có venv trên PATH, và
    `không có dòng line · grid 0/63` khi không có. Hai kết quả khác nhau cho
    cùng một mã nguồn, khác nhau vì một biến môi trường — đó là phép đo hỏng.

    `sys.executable` là interpreter đang chạy CERTUS, tức chính venv đã cài
    `coverage`/`pytest` trong requirements. Gọi `-m` thay vì tìm file
    `.venv/bin/pytest`: script wrapper có thể không tồn tại (cài bằng
    `--no-scripts`, hoặc Windows đặt tên khác), còn module thì luôn có.

    CHỈ áp dụng cho chế độ subprocess. Docker chạy trong image khác, ở đó
    đường dẫn của host không tồn tại — xem `_run_docker`.

    Allowlist đã chạy TRƯỚC hàm này (`run_probe`), trên tên logic. Thứ tự đó
    load-bearing: nếu đổi tên trước rồi mới kiểm thì `sys.executable` tên
    `python3.12` sẽ trượt allowlist `{"pytest","coverage","python"}`.
    """
    if not argv:
        return argv
    name = Path(argv[0]).name
    if name in _INTERPRETER_NAMES:
        return [sys.executable, *argv[1:]]
    if name in _INTERPRETER_MODULES:
        return [sys.executable, "-m", name, *argv[1:]]
    return argv


def _run_subprocess(
    workspace: Path, argv: list[str], *, cfg: ExecConfig, timeout_s: int
) -> tuple[int, str, str, bool, str | None]:
    run_dir = Path(tempfile.mkdtemp(prefix="certus-probe-"))
    try:
        proc = subprocess.run(  # noqa: S603 - argv đã qua _is_allowed
            _resolve_program(argv),
            cwd=str(workspace),
            env=_child_env(cfg, run_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr, False, None
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return EXIT_TIMEOUT, out, err, True, f"quá thời gian {timeout_s}s"
    except FileNotFoundError as exc:
        return EXIT_BLOCKED, "", str(exc), True, "không tìm thấy chương trình"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _run_docker(
    workspace: Path, argv: list[str], *, cfg: ExecConfig, timeout_s: int
) -> tuple[int, str, str, bool, str | None]:
    """Chạy trong container: mount read-only, không mạng, có trần tài nguyên.

    Dùng docker SDK chứ không gọi CLI `docker run`: SDK trả exit code và log ở
    dạng có cấu trúc, còn parse stdout của CLI là đúng thứ luật nhà cấm.
    """
    try:
        import docker  # type: ignore[import-not-found]
        from docker.errors import DockerException  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CertusError(
            "sandbox_mode='docker' nhưng chưa cài docker SDK. Đổi "
            "CERTUS_SANDBOX_MODE=subprocess hoặc cài `docker`."
        ) from exc

    limits = cfg.runner.docker
    container = None
    try:
        client = docker.from_env()
        container = client.containers.run(
            image=settings.sandbox_image,
            command=argv,
            working_dir="/workspace",
            volumes={str(workspace.resolve()): {"bind": "/workspace", "mode": "ro"}},
            environment=_child_env(cfg, Path("/tmp")),
            network_disabled=True,
            read_only=True,
            mem_limit=limits.mem_limit,
            pids_limit=limits.pids_limit,
            # Mount read-only nên mọi thứ ghi ra đều phải rơi vào tmpfs này.
            tmpfs={"/tmp": f"rw,size={limits.tmpfs_size_bytes}"},
            user=f"{os.getuid()}:{os.getgid()}",
            detach=True,
        )
        status = container.wait(timeout=timeout_s)
        exit_code = int(status.get("StatusCode", EXIT_BLOCKED))
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        return exit_code, stdout, stderr, False, None
    except DockerException as exc:
        # Sandbox không dựng được KHÔNG phải "test fail" — nó là UNVERIFIED.
        return EXIT_BLOCKED, "", str(exc), True, f"sandbox docker lỗi: {exc}"
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception as exc:  # noqa: BLE001 - dọn rác không được che lỗi chính
                import logging

                logging.getLogger(__name__).warning("không xoá được container: %s", exc)


def run_probe(
    workspace: Path,
    argv: list[str],
    *,
    ledger: EvidenceLedger | None = None,
    claim_id: str = "probe",
    timeout_s: int | None = None,
    config: ExecConfig | None = None,
) -> ProbeResult:
    """Chạy `argv` trong `workspace` và ghi đúng một record vào sổ bằng chứng.

    Ghi sổ nằm ở nhánh chung — lệnh bị chặn cũng có record, với verdict
    UNVERIFIED. "Không có gì để soi" phải NHÌN THẤY được trong sổ, chứ không
    được biểu hiện bằng sự vắng mặt của một dòng.
    """
    cfg = config or load_exec_config()
    book = ledger if ledger is not None else _resolve_ledger()
    limit = timeout_s if timeout_s is not None else settings.probe_timeout_seconds
    command = shlex.join(argv)

    started = time.perf_counter()
    if not _is_allowed(argv):
        reason = f"lệnh không nằm trong allowlist {sorted(ALLOWED_COMMANDS)}"
        exit_code, stdout, stderr, blocked, block_reason = (
            EXIT_BLOCKED,
            "",
            SandboxViolation(argv, reason).args[0],
            True,
            reason,
        )
    elif settings.sandbox_mode == "docker":
        exit_code, stdout, stderr, blocked, block_reason = _run_docker(
            workspace, argv, cfg=cfg, timeout_s=limit
        )
    else:
        exit_code, stdout, stderr, blocked, block_reason = _run_subprocess(
            workspace, argv, cfg=cfg, timeout_s=limit
        )
    duration_ms = int((time.perf_counter() - started) * 1000)

    stdout = _truncate(stdout, cfg.runner.max_output_bytes)
    stderr = _truncate(stderr, cfg.runner.max_output_bytes)

    if blocked:
        verdict = VERDICT_UNVERIFIED
    elif exit_code == 0:
        verdict = VERDICT_PASS
    else:
        verdict = VERDICT_FAIL

    evidence_id = book.append(
        claim_id=claim_id,
        command=command,
        exit_code=exit_code,
        output_sha256=_sha256(stdout + stderr),
        verdict=verdict,
    )

    return ProbeResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        blocked=blocked,
        block_reason=block_reason,
        command=command,
        evidence_id=evidence_id,
    )


__all__ = [
    "ALLOWED_COMMANDS",
    "CalibrationConfig",
    "CoverageConfig",
    "EvidenceLedger",
    "ExecConfig",
    "MutationConfig",
    "ProbeResult",
    "RunnerConfig",
    "exec_config_path",
    "load_exec_config",
    "run_probe",
]
