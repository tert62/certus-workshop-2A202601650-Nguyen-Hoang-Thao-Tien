/**
 * Port của hợp đồng SSE — SDD 00 §5.
 *
 * Một lần phân tích = một stream. Frontend parse theo `event:`.
 * Đúng 10 loại event, không thêm loại nào ở phía UI.
 *
 *   event: step     data: {"step": 3, "name": "enumerate_cells", "status": "running"}
 *   event: log      data: {"level": "INFO", "msg": "...", "trace_id": "..."}
 *   event: claim    data: <Claim JSON>
 *   event: cell     data: <Cell JSON>
 *   event: gate     data: <GateVerdict JSON>
 *   event: token    data: {"text": "..."}
 *   event: span     data: {"span_id","parent","name","ms","tokens"}
 *   event: warning  data: {"code": "cluster-floor", "msg": "..."}
 *   event: done     data: {"trace_id": "...", "claims": 12, "blocked": true}
 *   event: error    data: {"code": "...", "msg": "..."}
 */

import type { Cell, Claim, GateVerdict, RejectedClaim } from './contracts';

export interface StepPayload {
  step: number;
  name: string;
  status: 'running' | 'ok' | 'failed' | 'skipped';
}

export interface LogPayload {
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
  msg: string;
  trace_id?: string;
}

export interface TokenPayload {
  text: string;
}

/**
 * Hợp đồng §5 ghi đúng 5 khoá cho `event: span`. Đó là TẬP TỐI THIỂU.
 *
 * `trace_id` khai thêm ở đây là TUỲ CHỌN, vì `TraceViewer` cần nó để phát hiện
 * lỗi 11 (span của lời gọi LLM tự sinh trace mới ⇒ cây span đứt). Khi backend
 * không gửi, UI quy span về `trace_id` của `event: done` và nói rõ là suy ra —
 * tự sinh một id mới ở phía UI thì đúng bằng việc xoá dấu vết của chính lỗi đó.
 */
export interface SpanPayload {
  span_id: string;
  parent: string | null;
  name: string;
  ms: number;
  tokens: number | null;
  trace_id?: string;
  kind?: string;
  status?: string;
}

export interface WarningPayload {
  code: string;
  msg: string;
}

export interface DonePayload {
  trace_id: string;
  claims: number;
  /**
   * Câu mô hình đã nói mà validator backend từ chối. Đi cùng `claims` chứ không
   * ở một lời gọi REST riêng: đọc "12 claim" mà không thấy "6 câu bị loại" là
   * đọc một nửa kết quả.
   */
  rejected_claims?: RejectedClaim[];
  blocked: boolean;
}

export interface ErrorPayload {
  code: string;
  msg: string;
}

export type SseEvent =
  | { event: 'step'; data: StepPayload }
  | { event: 'log'; data: LogPayload }
  | { event: 'claim'; data: Claim }
  | { event: 'cell'; data: Cell }
  | { event: 'gate'; data: GateVerdict }
  | { event: 'token'; data: TokenPayload }
  | { event: 'span'; data: SpanPayload }
  | { event: 'warning'; data: WarningPayload }
  | { event: 'done'; data: DonePayload }
  | { event: 'error'; data: ErrorPayload };

export type SseEventName = SseEvent['event'];

const KNOWN: readonly string[] = [
  'step',
  'log',
  'claim',
  'cell',
  'gate',
  'token',
  'span',
  'warning',
  'done',
  'error',
];

export function isKnownEventName(name: string): name is SseEventName {
  return KNOWN.includes(name);
}
