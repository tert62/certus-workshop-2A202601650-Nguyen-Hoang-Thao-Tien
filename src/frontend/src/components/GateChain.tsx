/**
 * Panel 5 — chuỗi 5 cổng (SDD 08 §5.5).
 *
 * LUẬT VIẾT THÀNH CODE, KHÔNG PHẢI THÀNH HƯỚNG DẪN:
 *
 *   denominator === 0  ⇒  thẻ ĐỎ, BẤT KỂ verdict là gì
 *
 * Không có nhánh nào ở đây cho phép mẫu số 0 ra màu xanh. Đây là
 * `EmptyDenominatorError` của contracts/errors.py kéo lên tầng UI:
 * "Không có gì để soi là MỘT SỰ CỐ CẤU HÌNH, không phải một kết quả tốt."
 *
 * `compare_op` in ra chữ vì "dấu so sánh nằm trong hợp đồng, không để ngầm" —
 * một cổng `>= 0` và một cổng `> 0` cho verdict khác nhau trên cùng dữ liệu.
 */

import { Alert, Badge, Box, Card, Divider, Group, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconArrowRight, IconCheck, IconX } from '@tabler/icons-react';
import type { GateVerdict } from '@/types/contracts';
import { GATE_ORDER } from '@/types/contracts';

const EMPTY_DENOMINATOR_LINE =
  'chưa soi cái nào — đây là sự cố cấu hình, không phải kết quả tốt';

interface Props {
  gates: GateVerdict[];
}

export function GateChain({ gates }: Props) {
  // Chỉ nhận verdict ĐÚNG shape `GateVerdict` (có mảng `findings`). Backend nay
  // phát event `gate` đúng hợp đồng `types/sse.ts` — một `GateVerdict` cho mỗi
  // zone của cổng sàn. Bộ lọc vẫn giữ: nó là hàng rào chống một shape thứ hai
  // lẻn vào lần nữa, và cái shape cũ (dict sàn thô) từng làm panel này trống
  // trơn suốt cả luồng backend thật trong khi mock thì đầy đủ.
  const valid = gates.filter((g) => g && Array.isArray((g as GateVerdict).findings));
  const ordered = [...valid].sort(
    (a, b) => GATE_ORDER.indexOf(a.gate) - GATE_ORDER.indexOf(b.gate),
  );
  const empty = ordered.filter((g) => g.denominator === 0);

  if (valid.length === 0) {
    return (
      <Alert color="gray" variant="light" title="Chưa có cổng nào chạy">
        Chạy một lượt phân tích để xem chuỗi cổng.
      </Alert>
    );
  }

  return (
    <Stack gap="sm">
      <Box>
        <Title order={3}>Chuỗi cổng</Title>
        <Text size="sm" c="dimmed">
          Ba câu phải trả lời được cho mỗi cổng: ai gọi nó · nó so sánh bằng dấu gì · mẫu số của nó
          là bao nhiêu.
        </Text>
      </Box>

      {empty.length > 0 && (
        <Alert
          color="red"
          variant="filled"
          icon={<IconAlertTriangle size={18} />}
          title={`${empty.length} cổng có mẫu số bằng 0`}
        >
          <Text size="sm">
            {empty.map((g) => g.gate).join(', ')} — {EMPTY_DENOMINATOR_LINE}. Một cổng chạy trên 0
            symbol vẫn trả về verdict, và verdict đó không mang thông tin nào.
          </Text>
        </Alert>
      )}

      <Group gap={4} align="stretch" wrap="wrap">
        {/* Khoá gồm cả chỉ số: luồng analyze phát NHIỀU verdict cùng tên cổng
            (`grid`, một cái cho mỗi zone), nên khoá chỉ theo `gate.gate` sẽ
            trùng và React bỏ mất thẻ. */}
        {ordered.map((gate, i) => (
          <Group key={`${gate.gate}-${i}`} gap={4} align="stretch" wrap="nowrap">
            <GateCard gate={gate} />
            {i < ordered.length - 1 && (
              <Box style={{ display: 'flex', alignItems: 'center' }}>
                <IconArrowRight size={18} opacity={0.5} />
              </Box>
            )}
          </Group>
        ))}
      </Group>
    </Stack>
  );
}

function GateCard({ gate }: { gate: GateVerdict }) {
  // Mẫu số 0 chi phối mọi thứ khác. Thứ tự của hai dòng này là load-bearing.
  const emptyDenominator = gate.denominator === 0;
  const color = emptyDenominator ? 'red' : gate.verdict === 'pass' ? 'green' : 'orange';

  return (
    <Card
      withBorder
      radius="md"
      p="sm"
      w={228}
      style={{
        borderColor: `var(--mantine-color-${color}-6)`,
        borderWidth: 2,
      }}
    >
      <Stack gap={6} h="100%">
        <Group justify="space-between" align="center" wrap="nowrap">
          <Text fw={700} size="sm" ff="monospace">
            {gate.gate}
          </Text>
          <Badge
            color={color}
            size="sm"
            leftSection={
              emptyDenominator ? (
                <IconAlertTriangle size={11} />
              ) : gate.verdict === 'pass' ? (
                <IconCheck size={11} />
              ) : (
                <IconX size={11} />
              )
            }
          >
            {emptyDenominator ? 'VÔ HIỆU' : gate.verdict}
          </Badge>
        </Group>

        <Divider />

        <Group justify="space-between" gap={4}>
          <Text size="xs" c="dimmed">
            compare_op
          </Text>
          <Badge size="xs" variant="light" color="gray" ff="monospace">
            {gate.compare_op}
          </Badge>
        </Group>

        <Group justify="space-between" gap={4} align="baseline">
          <Text size="xs" c="dimmed">
            denominator
          </Text>
          <Text fw={700} ff="monospace" c={emptyDenominator ? 'red' : undefined} fz="lg">
            {gate.denominator}
          </Text>
        </Group>

        {emptyDenominator && (
          <Text size="xs" c="red" fw={600} lh={1.35}>
            {EMPTY_DENOMINATOR_LINE}
          </Text>
        )}

        <Group justify="space-between" gap={4}>
          <Text size="xs" c="dimmed">
            evidence_tier
          </Text>
          <Badge size="xs" variant="light" color={gate.evidence_tier ? 'certus' : 'red'}>
            {gate.evidence_tier ?? 'không có'}
          </Badge>
        </Group>

        <Group justify="space-between" gap={4}>
          <Text size="xs" c="dimmed">
            findings
          </Text>
          <Badge size="xs" variant="light" color={gate.findings.length > 0 ? 'orange' : 'gray'}>
            {gate.findings.length}
          </Badge>
        </Group>

        {gate.blocked && (
          <Badge size="xs" color="red" variant="filled">
            blocked
          </Badge>
        )}
        {gate.skipped && (
          <Badge size="xs" color="orange" variant="filled">
            skipped
          </Badge>
        )}

        {gate.reason && (
          <Text size="xs" c="dimmed" lh={1.3}>
            {gate.reason}
          </Text>
        )}

        {gate.findings.map((f, i) => (
          <Box key={`${f.rule_id}-${i}`}>
            <Text size="xs" ff="monospace" c="dimmed">
              {f.file}:{f.line}
            </Text>
            <Text size="xs" lh={1.3}>
              [{f.rule_id}] {f.finding}
            </Text>
          </Box>
        ))}
      </Stack>
    </Card>
  );
}
