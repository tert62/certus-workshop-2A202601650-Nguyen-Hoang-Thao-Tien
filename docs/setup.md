# Cài đặt

Mục tiêu: chạy được CERTUS trên máy bạn trong ~20 phút. Nếu quá 40 phút mà chưa xong, dừng lại và điền lỗi vào Form 2 — chúng tôi sẽ hỗ trợ trước buổi học.

## Yêu cầu

| | Phiên bản | Kiểm tra |
|---|---|---|
| Python | **3.11, 3.12 hoặc 3.13** — không phải 3.14 | `python3 --version` |
| Node.js | 18 trở lên | `node --version` |
| Git | bất kỳ | `git --version` |

Không cần Docker. Không cần API key.

> **Vì sao chặn trên ở 3.13.** `requirements.txt` ghim `scipy==1.15.0`, bản này
> chưa có wheel cho Python 3.14 nên pip rơi về build từ mã nguồn và chết ở bước
> sinh metadata (`Encountered error while generating package metadata: scipy`).
> Máy nào `python3 --version` ra 3.14 thì tạo venv bằng bản cũ hơn:
> `python3.12 -m venv .venv`.

## Bước 1 — Lấy mã nguồn

```bash
git clone <LINK_REPO>
cd certus-workshop
```

## Bước 2 — Backend

```bash
cd src/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Bước này tải khoảng 200 MB (`scipy`, `statsmodels`, `numpy`). Mạng chậm thì đây là chỗ lâu nhất.

## Bước 3 — Kiểm tra môi trường

```bash
python -m certus doctor
```

Lệnh này in ra đúng cái gì thiếu. **Dán nguyên văn output của nó vào Form 2 câu A5**, kể cả khi nó báo lỗi.

## Bước 4 — Chạy backend

```bash
uvicorn app.main:app --reload --port 8000
```

Mở http://localhost:8000/docs — thấy trang API là được.

## Bước 5 — Frontend

Mở terminal **thứ hai**:

```bash
cd src/frontend
npm install
npm run dev
```

Mở http://localhost:5173.

Mặc định frontend gọi **backend thật** (`VITE_USE_MOCK=0`). Không cần tạo `.env`
gì cả. Chỉ tạo `.env` khi bạn muốn xem UI mà chưa dựng được backend — xem mục
"Lỗi thường gặp" bên dưới.

## Bước 6 — Chạy thử

```bash
# terminal thứ ba
cd src/backend
.venv/bin/python -m certus analyze ../../fixtures/targets/shopcart
```

Phải ra **ba dòng mẫu số**, mỗi dòng kèm khoảng tin cậy:

```
  line_coverage       156/160   =  97.5%   wilson 95%: [93.7%, 99.0%]
  mutation_score        1/1     = 100.0%   wilson 95%: [20.7%, 100.0%]  [n-too-small, ...]
  grid_coverage        27/63    =  42.9%   wilson 95%: [31.4%, 55.1%]
```

Thiếu dòng `line_coverage`, hoặc `grid_coverage` ra `0/63`, nghĩa là probe không
chạy được — CERTUS sẽ **từ chối phân tích** và nói rõ lý do thay vì báo 0%.

## Bước 7 — Golden eval (không bắt buộc)

```bash
.venv/bin/python -m certus evals
```

Chạy lại 3 repo mẫu và so từng con số với `evals/golden.json`. Đây là lưới an
toàn: con số nào trôi thì nó đỏ, kể cả khi bộ test vẫn xanh.

---

## Chế độ LLM

Mặc định `CERTUS_LLM_MODE=mock` — dùng bản ghi có sẵn, **không cần API key**, và cả lớp thấy cùng một kết quả.

Ai có API key và muốn chạy thật:

```bash
export CERTUS_LLM_MODE=live
export CERTUS_ANTHROPIC_API_KEY=sk-ant-...
```

Không bắt buộc. Trong buổi học chỉ dùng ở phần demo trên sân khấu.

### Chạy live bằng gói Claude qua `ccs` + `cliproxy` (không cần API key trả tiền)

Nếu bạn đã có **gói đăng ký Claude** (Pro/Max) và dùng Claude Code, bạn có thể chạy
CERTUS ở chế độ `live` qua **cliproxy** — một proxy cục bộ bắc cầu SDK Anthropic sang
gói của bạn, KHÔNG tốn API key tính tiền. `ccs` là công cụ quản lý proxy đó.

1. Cài `ccs` (theo hướng dẫn của công cụ) rồi bật proxy cục bộ — nó lắng nghe ở
   `:8317`:

   ```bash
   ccs local          # để nguyên terminal này chạy
   ```

2. Terminal khác, nạp biến môi trường proxy rồi trỏ CERTUS vào:

   ```bash
   eval "$(ccs env local)"                       # cấp token + base URL của proxy
   export ANTHROPIC_BASE_URL=http://localhost:8317   # xem lưu ý (1) bên dưới
   export CERTUS_LLM_MODE=live
   export CERTUS_MODEL=claude-haiku-4-5           # xem lưu ý (2) bên dưới
   uvicorn app.main:app --reload --port 8000
   ```

   Kiểm tra nhanh không cần backend: `python -m certus analyze
   ../../fixtures/targets/shopcart` — ra bảng là proxy đã thông.

**Lưu ý — hai chỗ hay vấp (đã kiểm chứng thực tế):**

1. **Dùng `localhost`, đừng `127.0.0.1`.** Proxy thường chỉ lắng nghe trên IPv6
   (`[::1]`), nên `http://127.0.0.1:8317` sẽ báo *connection refused* còn
   `http://localhost:8317` (hoặc `http://[::1]:8317`) thì thông. `ccs env local` có
   thể tự đặt `127.0.0.1` — cứ export đè lại như trên.
2. **Chọn tên model "trần", tránh biến thể có hậu tố `[1m]`.** `claude-haiku-4-5`
   và `claude-opus-5` chạy tốt; biến thể cache như `claude-opus-5[1m]` có thể làm
   proxy **treo** (request không bao giờ trả về). Haiku rẻ + nhanh, hợp để thử;
   Opus mạnh hơn cho phần diễn giải nhưng tốn hơn.

Chế độ này hoàn toàn tùy chọn — mock vẫn là mặc định và đủ cho mọi bài trên lớp.
Repo mẫu (shopcart/ledger/payments) khoá trục cố định nên kết quả tất định; chỉ khi
bạn **tải repo của mình** lên thì mới bắt buộc qua bước HITL chọn trục.

---

## Lỗi thường gặp

**`pip install` treo ở scipy/numpy**
Mạng chậm. Thử `pip install -r requirements.txt --timeout 120`. Vẫn không được thì dùng mirror trong nước:
```bash
pip install -r requirements.txt -i https://pypi.org/simple --retries 5
```

**`ModuleNotFoundError: No module named 'app'`**
Bạn đang không ở `src/backend`. Mọi lệnh Python phải chạy từ đó.

**`.venv\Scripts\activate` bị Windows chặn**
PowerShell với quyền admin:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**`npm install` lỗi peer dependency**
```bash
npm install --legacy-peer-deps
```

**Cổng 8000 hoặc 5173 đã bị chiếm**
```bash
uvicorn app.main:app --port 8001
npm run dev -- --port 5174
```
Nếu đổi cổng backend, sửa `src/frontend/vite.config.ts` (proxy `/api`) hoặc đặt
`VITE_API_BASE=http://localhost:8001` trong `src/frontend/.env`. Tên biến là
`VITE_API_BASE` — không phải `VITE_API_URL`.

**Xem giao diện mà chưa chạy được backend**
```bash
cd src/frontend && VITE_USE_MOCK=1 npm run dev
```
Frontend có sẵn dữ liệu giả lập, xem được toàn bộ UI. Nhớ **tắt lại** khi backend
đã chạy: một UI mock trông giống hệt UI thật, và đó là lý do mặc định của nó là 0.

**Bảng kết quả thiếu dòng `line_coverage`, grid ra 0%**
Lượt cũ (trước bản sửa) im lặng trả 0% khi probe không chạy được. Bản hiện tại
từ chối phân tích và in lý do. Chạy `python -m certus doctor` — mục
`probe chạy được` phải OK.

---

## Chạy test

```bash
cd src/backend
.venv/bin/python -m pytest ../../tests/ -q
```

Toàn bộ phải xanh. Nếu có test đỏ ngay sau khi clone, đó là lỗi môi trường — báo cho chúng tôi.

## Nếu vẫn không được

Điền Form 2 phần A với:
- output của `python -m certus doctor`
- nguyên văn thông báo lỗi
- hệ điều hành và phiên bản Python/Node

Đừng mất quá 40 phút. Buổi học có phương án cho người chưa cài được.
