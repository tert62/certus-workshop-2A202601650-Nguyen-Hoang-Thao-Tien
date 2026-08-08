/**
 * Port 1-1 của `app/contracts/types.py` (SDD 00 §3) sang TypeScript.
 *
 * Không thêm trường, không đổi tên trường. Enum bên Python là `StrEnum` nên
 * bên này là union của string literal — giá trị dây giữ nguyên, kể cả `"N/A"`
 * với dấu gạch chéo.
 *
 * Chỗ này KHÔNG khai lại một enum nào của backend theo cách khác đi: một enum
 * bị khai hai nơi là cách kinh điển để hai nửa hệ thống trôi khỏi nhau mà
 * không ai thấy.
 */

/** Nhãn bằng chứng của một claim. */
export type Label = 'OBSERVED' | 'DERIVED' | 'PRIOR' | 'ASSUMED';

export const LABELS: readonly Label[] = ['OBSERVED', 'DERIVED', 'PRIOR', 'ASSUMED'];

/**
 * "Không đủ tư cách mang bất kỳ nhãn nào" — tầng meta, KHÔNG cùng trục với
 * Label. Đây là một phán quyết HỢP LỆ, không phải một thất bại.
 */
export const UNVERIFIED = 'UNVERIFIED';

/** Mức phủ của một grid cell. Luôn suy diễn, không bao giờ do model chọn. */
export type Band = 'high' | 'med' | 'low' | 'stub' | 'N/A' | 'unknown';

export const BANDS: readonly Band[] = ['high', 'med', 'low', 'stub', 'N/A', 'unknown'];

/** Cố ý KHÔNG có "asserted" — nói suông bị cấm làm cơ sở kết luận. */
export type EvidenceTier = 'executed' | 'retrieved' | 'derived';

export type GateName = 'requirements' | 'design' | 'grid' | 'execution' | 'outcome';

/** Thứ tự của chuỗi cổng là một quyết định, không phải trình bày. */
export const GATE_ORDER: readonly GateName[] = [
  'requirements',
  'design',
  'grid',
  'execution',
  'outcome',
];

export type IntervalMethod = 'wilson' | 'wilson-cc' | 'clopper-pearson' | 'jeffreys';

export type ClusterRoute = 'icc' | 'icc-upper' | 'cluster-floor';

/**
 * Khoảng tin cậy cho một tỉ lệ.
 *
 * KHÔNG có trường `confidence`. Đó là chỗ lỗi 5 sống bên backend
 * (`api/schemas.py` serialize `p_hat` ra một field tên `confidence`); kiểu ở
 * đây từ chối nó trước, nên khi backend sửa xong frontend không phải sửa theo.
 *
 * `saturated` không có nghĩa là "hẹp" — nó có nghĩa interval đã TRÀN ra ngoài
 * [0,1] rồi bị cắt về. Một interval tệ trông hẹp nhất bảng đúng vì lý do này.
 */
export interface Interval {
  lower: number;
  upper: number;
  conf: number;
  method: IntervalMethod;
  n: number;
  k: number;
  /** cỡ mẫu hiệu dụng sau cluster correction */
  n_eff?: number | null;
  route?: ClusterRoute | null;
  saturated: boolean;
}

/** Neo bằng chứng. Claim không có neo là UNVERIFIABLE. */
export interface Anchor {
  kind: 'file_line' | 'command' | 'artifact';
  /** "src/cart.py:42" | "pytest -q" | "sha256:abc..." */
  ref: string;
  exit_code?: number | null;
}

/** Một câu CERTUS nói về code của bạn. */
export interface Claim {
  id: string;
  text: string;
  label: Label;
  k?: number | null;
  n?: number | null;
  interval?: Interval | null;
  /** hash record trong evidence ledger */
  evidence_ids: string[];
  anchors: Anchor[];
  /** saturated | cluster-floor | judge-rejected | prior-used | n-too-small */
  flags: string[];
  is_rate: boolean;
  /** phải nêu được cơ chế thì mới có tư cách DERIVED */
  mechanism?: string | null;
}

/** Một ô của lưới — một phép gán đầy đủ cho một t-subset của axes. */
export interface Cell {
  /** cell:<axis>=<v>|<axis>=<v> — canonical, không tự đặt */
  id: string;
  axes: Record<string, string>;
  zone_id: string;
  zone_w: number;
  band: Band;
  /** giá trị DUY NHẤT được chấp nhận */
  source: 'projected';
  flags: string[];
  evidence_id: string[];
}

/** `file` và `line` là BẮT BUỘC — finding không neo được thì downstream bó tay. */
export interface Finding {
  rule_id: string;
  severity: 'info' | 'warn' | 'error';
  file: string;
  line: number;
  finding: string;
}

/**
 * Phán quyết của một cổng.
 *
 * `verdict` chỉ có MỘT hệ {pass, fail}. `denominator` là `symbols_scanned`:
 * 0 nghĩa là ĐỎ, không phải xanh.
 */
export interface GateVerdict {
  gate: GateName;
  verdict: 'pass' | 'fail';
  evidence_tier?: EvidenceTier | null;
  findings: Finding[];
  /** dấu so sánh nằm trong hợp đồng, không để ngầm */
  compare_op: string;
  denominator: number;
  blocked: boolean;
  skipped: boolean;
  reason?: string | null;
}

/**
 * Một câu mô hình đã nói mà validator của backend TỪ CHỐI cho vào `claims`.
 *
 * Có kiểu riêng vì nó không phải một `Claim` bị hạ cấp — nó chưa bao giờ là
 * claim. Hiển thị nó là để người đọc thấy QUY TẮC đang thi hành; im lặng bỏ đi
 * chỉ để lại một đoạn văn ngắn hơn bình thường.
 */
export interface RejectedClaim {
  id: string;
  text: string;
  label: string;
  reason: string;
}

/** Một dòng của sổ bằng chứng append-only (SDD 00 §5.4). */
export interface LedgerRecord {
  claim_id: string;
  command: string;
  exit_code: number | null;
  output_sha256: string;
  verdict: 'executed-pass' | 'executed-fail' | 'UNVERIFIED';
  prev_hash: string;
  self_hash: string;
  ts: string;
  actor?: string | null;
}
