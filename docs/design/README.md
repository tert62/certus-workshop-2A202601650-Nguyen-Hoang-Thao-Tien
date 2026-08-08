# docs/design — bản đồ tham chiếu

Mã nguồn của CERTUS trích dẫn một bộ tài liệu thiết kế (`docs/design/sdd/NN-*.md`,
`docs/design/system-design.md`, `docs/workshop-plan.md`) **không có mặt trong repo
này**. Repo phát cho lớp học là bản rút gọn: nó mang mã nguồn, cấu hình, KB và
hai research note, không mang cây tài liệu thiết kế nội bộ.

Tệp này tồn tại để một trích dẫn treo không đọc thành "tài liệu bị mất". Nó nói
rõ mỗi tham chiếu đáng ra chứa gì, và **nội dung đó hiện sống ở đâu**.

`tests/test_doc_refs.py` ghim danh sách dưới đây: xuất hiện một đường dẫn
`docs/...` mới mà không tồn tại và không có trong bảng ⇒ bộ kiểm ĐỎ.

## Bảng tham chiếu

| Trích dẫn trong mã | Đáng ra chứa gì | Đọc thay ở đâu |
|---|---|---|
| `docs/design/sdd/00-index.md` §2, §4, §5 | thứ tự lô build · luật ba vế của ngưỡng · hợp đồng SSE 10 loại sự kiện | luật ba vế: phần đầu mỗi tệp `src/backend/config/*.yaml`. Hợp đồng SSE: `app/api/schemas.py::StreamEvent` và `src/frontend/src/types/sse.ts` |
| `docs/design/sdd/01-core-stats.md` | Wilson · cluster · judge correction | `docs/research-notes/01-confidence-intervals.md` §1 (công thức code-ready) |
| `docs/design/sdd/03-core-exec.md` §5, §8 | cách gọi mutmut · giới hạn sandbox | docstring `app/core/exec/mutate.py` (đã ghi lại kết quả đo trên mutmut 3.2.0) và `app/core/exec/runner.py` |
| `docs/design/sdd/06-platform.md` | evidence ledger · redaction | `app/ledger/evidence.py`, `app/policy/redaction.py`, `src/backend/config/data-policy.yaml` |
| `docs/design/sdd/08-frontend.md` §3–§7 | bố cục panel · công tắc mock | docstring đầu mỗi component trong `src/frontend/src/components/` |
| `docs/design/system-design.md` §2.1, §5.1, §7 | đánh đổi sandbox · Claim là đơn vị · quyền ghi config theo vai | `app/settings.py` (sandbox), `app/contracts/types.py::Claim`, `src/backend/config/auth.yaml` |
| `docs/workshop-plan.md` | kịch bản buổi học | `kb/README.md` mục "Ghi chú cho người dựng workshop" |
| `docs/research/methodology/...` | tài liệu nền gốc | `docs/research-notes/01-confidence-intervals.md` (đã tóm tắt kèm số dòng gốc) |

## Vì sao không xoá các trích dẫn đi cho gọn

Một trích dẫn treo nói được một điều mà sự im lặng không nói được: **con số này
đến từ đâu đó, và chỗ đó có tên**. Xoá đi thì mỗi ngưỡng trong `config/*.yaml`
mất vế "suy ra từ đâu" — đúng cái luật ba vế mà chính các tệp đó cưỡng chế.

## Vì sao không viết bù các tài liệu đó

Viết lại một SDD từ mã nguồn cho ra một tài liệu MÔ TẢ mã hiện tại, trong khi
bản gốc là tài liệu QUYẾT ĐỊNH — nó ghi các phương án đã cân nhắc và lý do loại.
Hai thứ trông giống nhau và chỉ một trong hai dùng được khi cần đổi quyết định.
Bảng trên trung thực hơn: nó nói "chỗ này thiếu, và đây là thứ gần nhất".
