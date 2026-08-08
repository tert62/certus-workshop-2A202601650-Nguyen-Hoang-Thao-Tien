# CERTUS — Trợ lý QA phân tích độ phủ kiểm thử

CERTUS nhận mã nguồn, chạy bộ kiểm, đọc độ phủ và diễn giải bằng hội thoại: phần
nào đã phủ, phần nào còn hở, con số nói lên điều gì (line coverage · mutation
score · grid coverage) và độ tin của chúng.

Ba mẫu số luôn đứng **cạnh nhau và không gộp**, mỗi cái kèm k/n và khoảng Wilson.
Không có con số tổng hợp nào thay được cả ba — một repo đạt 100% line coverage
vẫn có thể có 0% grid coverage, và đó là ca đáng lo nhất chứ không phải ca tốt.

## Chạy

Backend (FastAPI) — Python **3.11–3.13**:

```bash
cd src/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest ../../tests -q      # bộ test
python -m certus doctor              # kiểm môi trường
python -m certus evals               # golden eval
uvicorn app.main:app --reload        # API
```

Frontend (React):

```bash
cd src/frontend
npm install
npm run dev
```

Frontend mặc định gọi backend thật; `VITE_USE_MOCK=1` mới bật dữ liệu giả lập.

## Có gì trong này

| | |
|---|---|
| **Phân tích** | `POST /api/analyze` (SSE) · `python -m certus analyze <repo>` — chạy bộ kiểm, chiếu lên lưới trục×zone, chấm sàn từng zone |
| **Hội thoại** | `POST /api/chat/stream` — hỏi tiếp nhiều lượt trên cùng một kết quả |
| **Chọn trục (HITL)** | `POST /api/axes/discover` — repo lạ phải chốt 2–4 trục trước khi phân tích; repo mẫu khoá trục sẵn để bài giảng tất định |
| **Chế độ mô hình** | Công tắc cassette ⇄ live ngay trên header, không cần khởi động lại |
| **Sổ bằng chứng** | `GET /api/ledger` — mọi lượt chạy probe đều để lại một record, kể cả lượt bị chặn |
| **Golden eval** | `evals/run.py` — ghim con số của 3 repo mẫu và số claim bị từ chối trong cassette |

## Chế độ LLM

Mặc định `CERTUS_LLM_MODE=mock`: dùng cassette trong `fixtures/cassettes/`, không
cần API key, cả lớp thấy cùng một kết quả. Xem `docs/setup.md` để chạy `live`
(kể cả bằng gói đăng ký Claude qua `ccs`/`cliproxy`).

## Tài liệu

- `docs/setup.md` — cài đặt, lỗi thường gặp, chế độ live
- `docs/research-notes/` — nền lý thuyết (khoảng tin cậy Wilson · grid coverage · chuỗi cổng QA)
- `kb/` — knowledge base mà CERTUS được phép trích dẫn
