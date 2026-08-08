/**
 * Panel 6 — bảng mọi claim (SDD 08 §5.6).
 *
 * QUY TẮC PHÁT HIỆN LỖI 6 NẰM NGAY TRONG BẢNG:
 *
 *   label === "OBSERVED" && evidence_ids rỗng && anchors rỗng
 *       ⇒ viền đỏ quanh hàng + icon cảnh báo + một dòng chữ
 *
 * Vi phạm bị chỉ đích danh: "Only a tool promotes a claim. … restating it more
 * confidently does NOT — that is a hallucination wearing OBSERVED grammar."
 *
 * Dòng đếm trên đầu bảng dùng CHUNG hàm `isUnanchoredObserved` với từng hàng,
 * vì hai chỗ định nghĩa "claim hỏng" theo hai kiểu là cách con số trên đầu
 * bảng lệch khỏi số hàng viền đỏ bên dưới.
 */

import { Alert, Badge, Box, Group, Paper, Stack, Table, Text, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import type { Claim, RejectedClaim } from '@/types/contracts';
import { UNVERIFIED } from '@/types/contracts';
import { isMalformedRateClaim, isUnanchoredObserved, LABEL_STYLES } from '@/lib/labels';
import { fraction } from '@/lib/format';
import { IntervalBadge } from './IntervalBadge';
import { FlagList } from './FlagList';

const UNANCHORED_LINE =
  'OBSERVED mà không có evidence — chỉ tool mới thăng hạng được một claim';

interface Props {
  claims: Claim[];
  /**
   * Câu backend đã TỪ CHỐI. Mặc định rỗng để nơi gọi cũ không vỡ, nhưng rỗng ở
   * đây phải nghĩa là "không câu nào bị từ chối", KHÔNG phải "chưa ai truyền
   * vào" — nên `App.tsx` luôn truyền tường minh.
   */
  rejected?: RejectedClaim[];
}

export function ClaimInspector({ claims, rejected = [] }: Props) {
  const unanchored = claims.filter(isUnanchoredObserved);

  if (claims.length === 0 && rejected.length === 0) {
    return (
      <Alert color="gray" variant="light" title="Chưa có claim nào">
        Chạy một lượt phân tích để xem các claim mà CERTUS phát biểu.
      </Alert>
    );
  }

  return (
    <Stack gap="sm">
      <Box>
        <Title order={3}>Claim inspector</Title>
        <Text size="sm" c="dimmed">
          Mọi câu CERTUS nói về code của bạn đều là một claim, và mỗi claim mang đúng một nhãn.
        </Text>
      </Box>

      {unanchored.length > 0 && (
        <Alert
          color="red"
          variant="filled"
          icon={<IconAlertTriangle size={18} />}
          title={`${unanchored.length}/${claims.length} claim mang nhãn OBSERVED mà không có evidence`}
        >
          <Text size="sm">
            Nhãn trả lời câu hỏi "có chạy thật không". Một claim OBSERVED không neo được vào bản ghi
            nào trong evidence ledger thì nhãn đó không do tool cấp — nó do mô hình tự ghi. Khi phần
            lớn claim đều xanh lá theo kiểu này, cả hệ thống nhãn trở thành trang trí.
          </Text>
        </Alert>
      )}

      <Group gap="xs">
        {Object.values(LABEL_STYLES).map((style) => {
          const count = claims.filter((c) => c.label === style.label).length;
          return (
            <Tooltip key={style.label} label={style.meaning} multiline w={280}>
              <Badge color={style.color} variant="light" size="sm">
                {style.label}: {count}
              </Badge>
            </Tooltip>
          );
        })}
      </Group>

      <Paper withBorder radius="md" p={0}>
        <Table.ScrollContainer minWidth={880}>
          <Table striped highlightOnHover verticalSpacing="sm" fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={110}>Nhãn</Table.Th>
                <Table.Th>Claim</Table.Th>
                <Table.Th w={90}>k/n</Table.Th>
                <Table.Th w={280}>Khoảng tin cậy</Table.Th>
                <Table.Th w={90}>Evidence</Table.Th>
                <Table.Th w={180}>Cờ</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {claims.map((claim) => (
                <ClaimRow key={claim.id} claim={claim} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>

      {rejected.length > 0 && <RejectedTable rejected={rejected} />}
    </Stack>
  );
}

/**
 * Bảng "câu bị chặn ở cửa".
 *
 * Vì sao hiển thị thay vì bỏ đi: mô hình nói N câu, bảng trên chỉ có M < N, và
 * chênh lệch đó trước đây không xuất hiện ở đâu trên UI — người đọc thấy một
 * câu trả lời ngắn, không thấy một cái cổng đang làm việc. Đo trên cassette
 * đang commit: 3–6 câu mỗi lượt.
 *
 * Bảng này KHÔNG dùng `LABEL_STYLES` (không tô màu nhãn) — cố ý. Tô nhãn cho
 * một câu đã bị từ chối là trả lại cho nó đúng thứ uy tín mà cửa vừa lấy đi.
 */
function RejectedTable({ rejected }: { rejected: RejectedClaim[] }) {
  return (
    <Stack gap="xs">
      <Alert
        color="orange"
        variant="light"
        icon={<IconAlertTriangle size={18} />}
        title={`${rejected.length} câu bị chặn ở cửa, không hiển thị như claim`}
      >
        <Text size="sm">
          Mô hình đã nói những câu dưới đây, nhưng chúng không qua được validator của hệ nên không
          được tính là claim. Chúng nằm đây làm TANG VẬT, không phải kết luận — đừng trích dẫn.
        </Text>
      </Alert>

      <Paper withBorder radius="md" p={0}>
        <Table.ScrollContainer minWidth={720}>
          <Table striped verticalSpacing="sm" fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={90}>Nhãn khai</Table.Th>
                <Table.Th>Câu bị từ chối</Table.Th>
                <Table.Th w={300}>Lý do từ chối</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rejected.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>
                    <Badge color="gray" variant="outline" size="sm">
                      {r.label}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" td="line-through" c="dimmed">
                      {r.text}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" ff="monospace" c="orange.8">
                      {r.reason}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
    </Stack>
  );
}

function ClaimRow({ claim }: { claim: Claim }) {
  const broken = isUnanchoredObserved(claim);
  const malformed = isMalformedRateClaim(claim);
  const style = LABEL_STYLES[claim.label];

  return (
    <Table.Tr
      style={
        broken
          ? {
              outline: '2px solid var(--mantine-color-red-6)',
              outlineOffset: -2,
              background: 'var(--mantine-color-red-light)',
            }
          : undefined
      }
    >
      <Table.Td>
        <Group gap={4} wrap="nowrap">
          {broken && <IconAlertTriangle size={15} color="var(--mantine-color-red-6)" />}
          <Tooltip label={style.meaning} multiline w={280}>
            <Badge color={style.color} size="sm">
              {claim.label}
            </Badge>
          </Tooltip>
        </Group>
      </Table.Td>

      <Table.Td>
        <Text size="sm">{claim.text}</Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {claim.id}
        </Text>
        {broken && (
          <Text size="xs" c="red" fw={650} mt={2}>
            {UNANCHORED_LINE}
          </Text>
        )}
        {malformed && (
          <Text size="xs" c="red" fw={650} mt={2}>
            MALFORMED CLAIM — claim tỉ lệ gắn nhãn OBSERVED mà thiếu k/n hoặc interval
          </Text>
        )}
        {claim.mechanism && (
          <Text size="xs" c="dimmed" mt={2}>
            cơ chế: {claim.mechanism}
          </Text>
        )}
        {claim.anchors.length > 0 && (
          <Group gap={4} mt={4}>
            {claim.anchors.map((a, i) => (
              <Badge key={`${a.ref}-${i}`} size="xs" variant="outline" ff="monospace">
                {a.kind}: {a.ref}
                {a.exit_code != null ? ` (exit ${a.exit_code})` : ''}
              </Badge>
            ))}
          </Group>
        )}
      </Table.Td>

      <Table.Td>
        {claim.k != null && claim.n != null ? (
          <Text ff="monospace" fw={600} size="sm">
            {fraction(claim.k, claim.n)}
          </Text>
        ) : (
          <Text size="xs" c="dimmed">
            không phải tỉ lệ
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        {claim.interval ? (
          <IntervalBadge interval={claim.interval} showFraction={false} size="xs" />
        ) : (
          <Text size="xs" c="dimmed">
            —
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        {claim.evidence_ids.length === 0 ? (
          <Badge size="xs" color="red" ff="monospace">
            {UNVERIFIED}
          </Badge>
        ) : (
          <Tooltip label={claim.evidence_ids.join(', ')}>
            <Badge size="xs" color="green" variant="light">
              {claim.evidence_ids.length}
            </Badge>
          </Tooltip>
        )}
      </Table.Td>

      <Table.Td>
        <FlagList flags={claim.flags} />
      </Table.Td>
    </Table.Tr>
  );
}
