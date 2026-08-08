"""Kiểm tra sức khoẻ — và nói thật về những gì CHƯA sẵn sàng."""

from __future__ import annotations

import subprocess
import sys

from fastapi import APIRouter

from app.settings import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Trạng thái sống. Không kiểm gì sâu — đó là việc của /doctor."""
    return {"status": "ok", "llm_mode": settings.llm_mode}


@router.get("/doctor")
def doctor() -> dict:
    """Liệt kê từng thứ và nói rõ thiếu gì.

    Trả về `checks` dạng danh sách chứ không phải một chữ "ok": một endpoint chỉ
    biết trả lời ok/không-ok thì khi nó nói không-ok, người đọc vẫn không biết
    phải đi sửa cái gì.
    """
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    for label, path in (
        ("config", settings.config_dir),
        ("repo mẫu", settings.targets_dir),
        ("knowledge base", settings.kb_dir),
        ("cassette", settings.cassette_dir),
    ):
        add(label, path.is_dir(), f"{path} {'có' if path.is_dir() else 'KHÔNG có'}")

    if settings.llm_mode == "live":
        add(
            "API key",
            bool(settings.anthropic_api_key),
            "chế độ live cần CERTUS_ANTHROPIC_API_KEY",
        )
    else:
        add("API key", True, f"chế độ {settings.llm_mode} không cần key")

    for mod in ("scipy", "statsmodels", "coverage", "pytest", "anthropic"):
        try:
            __import__(mod)
            add(mod, True, "đã cài")
        except ImportError:
            add(mod, False, f"thiếu — chạy `pip install {mod}`")

    # Phiên bản Python: `requirements.txt` ghim scipy==1.15.0, và bản đó KHÔNG có
    # wheel cho 3.14 — pip rơi về build from source rồi chết ở bước sinh metadata.
    # Kiểm ở đây vì triệu chứng thật xảy ra lúc `pip install`, tức TRƯỚC khi
    # doctor chạy được; ai đã cài xong bằng cách nào đó trên 3.14 vẫn nên thấy
    # cảnh báo thay vì gặp lỗi lạ ở tầng scipy.
    version = sys.version_info
    add(
        "phiên bản Python",
        (3, 11) <= (version.major, version.minor) <= (3, 13),
        f"{version.major}.{version.minor}.{version.micro} — dải hỗ trợ là 3.11–3.13 "
        f"(scipy 1.15.0 chưa có wheel cho 3.14)",
    )

    # Probe chạy được thật chưa. Ba lớp kiểm ở trên chỉ nói "import được"; lớp này
    # nói "tiến trình con chạy được". Chúng khác nhau: `run_probe` gọi
    # `sys.executable -m coverage` trong một tiến trình riêng với PATH và biến môi
    # trường bị lọc, nên một máy import ổn vẫn có thể không chạy nổi probe — và
    # khi đó mọi mẫu số về 0 (xem `orchestrator/pipeline.run_target_suite`).
    try:
        proc = subprocess.run(  # noqa: S603 - argv hằng số, không có đầu vào người dùng
            [sys.executable, "-m", "coverage", "--version"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        add(
            "probe chạy được",
            proc.returncode == 0,
            (proc.stdout or proc.stderr).strip().splitlines()[0]
            if (proc.stdout or proc.stderr)
            else f"exit {proc.returncode}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        add("probe chạy được", False, f"không chạy nổi tiến trình con: {exc}")

    failed = [c["name"] for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "checks": checks,
        "denominator": len(checks),
        "failed": failed,
    }
