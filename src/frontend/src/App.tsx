/**
 * Khung ứng dụng — SDD 08 §3.
 *
 * Bố cục: header + cột nội dung (Tabs) + cột cảnh báo luôn hiển thị.
 *
 * `WarningFeed` KHÔNG nằm trong Tabs. Nó đứng cạnh mọi tab, vì một cảnh báo
 * chỉ thấy được khi đang mở đúng tab là một cảnh báo sẽ bị bỏ lỡ.
 *
 * Tab "Dữ liệu đã gửi cho mô hình" là tab PHỤ và không phải `defaultValue` —
 * đúng đặc tả lỗi 8.
 */

import { useCallback, useRef, useState } from 'react';
import {
  ActionIcon,
  Alert,
  AppShell,
  Badge,
  Button,
  Container,
  Grid,
  Group,
  Paper,
  Stack,
  Tabs,
  Text,
  Title,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import {
  IconAdjustments,
  IconChartGridDots,
  IconFileDescription,
  IconLayoutGrid,
  IconListCheck,
  IconMessage,
  IconMessages,
  IconMoon,
  IconPlayerPlay,
  IconPlayerStop,
  IconRoute,
  IconShieldLock,
  IconSun,
  IconUpload,
} from '@tabler/icons-react';

import { ModeSwitch } from '@/components/ModeSwitch';
import { UploadPanel } from '@/components/UploadPanel';
import { AxisSelectionPanel } from '@/components/AxisSelectionPanel';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { ChatPanel } from '@/components/ChatPanel';
import { ChatConversation } from '@/components/ChatConversation';
import { CoverageTriptych } from '@/components/CoverageTriptych';
import { GridHeatmap } from '@/components/GridHeatmap';
import { GateChain } from '@/components/GateChain';
import { ClaimInspector } from '@/components/ClaimInspector';
import { TraceViewer } from '@/components/TraceViewer';
import { PromptDataPanel } from '@/components/PromptDataPanel';
import { WarningFeed } from '@/components/WarningFeed';
import { StepProgress } from '@/components/StepProgress';

import { getCoverage, getPromptPayload, openAnalyzeStream, USE_MOCK } from '@/api/analyze';
import { useAnalysisStore } from '@/store/analysisStore';
import type { UploadResult } from '@/types/api';
import { TAGLINE } from '@/theme';

export default function App() {
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme('light');

  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [question, setQuestion] = useState('Bộ kiểm thử của repo này phủ tới đâu?');
  const [confirmedAxes, setConfirmedAxes] = useState<Record<string, string[]> | null>(null);
  const [tab, setTab] = useState<string>('upload');
  const abortRef = useRef<AbortController | null>(null);

  const store = useAnalysisStore();
  const finished = store.status === 'done';

  // Backend lưu kết quả theo `run_id = trace_id` (id của LẦN CHẠY), không theo
  // tên repo mẫu. Trước đây hỏi coverage/payload bằng `upload.run_id` (tên repo)
  // nên với backend thật luôn trả rỗng. Ưu tiên `trace_id` mà lượt chạy vừa phát.
  const resolvedRunId = store.done?.trace_id ?? upload?.run_id;

  const coverageQuery = useQuery({
    queryKey: ['coverage', resolvedRunId, finished],
    queryFn: ({ signal }) => getCoverage(resolvedRunId!, signal),
    enabled: Boolean(resolvedRunId) && finished,
  });

  const promptQuery = useQuery({
    queryKey: ['prompt-payload', resolvedRunId, finished],
    queryFn: ({ signal }) => getPromptPayload(resolvedRunId!, signal),
    enabled: Boolean(resolvedRunId) && finished,
  });

  const run = useCallback(async () => {
    if (!upload) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const { start, apply, finish } = useAnalysisStore.getState();
    start(upload.run_id, question);
    setTab('coverage');

    // Backend `AnalyzeRequest` nhận đúng MỘT trong `target` (repo mẫu) hoặc
    // `upload_id` (tệp vừa tải lên thật) — không có trường `run_id`. Repo mẫu
    // không đi qua `/api/upload` nên chỉ có `target`; tệp .zip thật thì
    // `client.uploadZip` đã gắn `upload_id` từ `UploadAck` trả về.
    const req = {
      ...(upload.target
        ? { target: upload.target }
        : { upload_id: upload.upload_id ?? upload.run_id }),
      question,
      // HITL: gửi confirmed_axes khi người dùng đã chốt. Repo mẫu bỏ trống ⇒ engine
      // dùng đúng tập Enum cố định (cassette bất biến). Repo thật BỎ TRỐNG bị backend
      // chặn — nút chạy đã khóa tới khi chốt trục (needsAxisSelection).
      ...(confirmedAxes ? { confirmed_axes: confirmedAxes } : {}),
    };

    await openAnalyzeStream(
      req,
      { onEvent: apply, signal: controller.signal },
    );
    finish();
  }, [upload, question, confirmedAxes]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    useAnalysisStore.getState().finish();
  }, []);

  // Repo THẬT (upload, không phải repo mẫu) BẮT BUỘC chốt trục trước khi chạy —
  // backend cũng gate (CertusError), nhưng khóa nút ở đây để người dùng không đâm
  // vào lỗi. Repo mẫu (`target`) miễn: trục đã cố định, panel chỉ để xem.
  const needsAxisSelection = Boolean(upload) && !upload?.target && !confirmedAxes;
  const runDisabled = !upload || needsAxisSelection;

  const handleUpload = useCallback((result: UploadResult) => {
    setUpload(result);
    setConfirmedAxes(null); // repo mới → trả quyền chọn trục cho engine
    useAnalysisStore.getState().reset();
  }, []);

  return (
    <AppShell header={{ height: 62 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Title order={3}>CERTUS</Title>
            <Text size="sm" c="dimmed">
              {TAGLINE}
            </Text>
          </Group>
          <Group gap="xs">
            {USE_MOCK ? (
              <Tooltip label="VITE_USE_MOCK=1 — stream đang được phát lại từ api/mock.ts, không phải từ backend. Công tắc cassette/live không áp dụng ở chế độ này.">
                <Badge color="orange" variant="light">
                  dữ liệu giả lập
                </Badge>
              </Tooltip>
            ) : (
              <ModeSwitch />
            )}
            {store.done && (
              <Badge variant="light" ff="monospace">
                trace: {store.done.trace_id}
              </Badge>
            )}
            <ActionIcon
              variant="default"
              size="lg"
              aria-label="Chuyển sáng/tối"
              onClick={() => setColorScheme(scheme === 'dark' ? 'light' : 'dark')}
            >
              {scheme === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
            </ActionIcon>
          </Group>
        </Group>
      </AppShell.Header>

      {/* Khoá chiều cao vào viewport + cắt tràn: KHÔNG để cả trang cuộn. Mỗi cột
          tự cuộn nội bộ (dưới đây), nên header + tab-list đứng yên còn nội dung
          dài thì cuộn trong cột của nó. */}
      <AppShell.Main style={{ height: '100dvh', overflow: 'hidden' }}>
        <Container fluid px={0} h="100%">
          <Grid gutter="md" h="100%">
            <Grid.Col
              span={{ base: 12, lg: 9 }}
              style={{ maxHeight: 'calc(100dvh - 94px)', overflowY: 'auto' }}
            >
              <ErrorBoundary resetKey={tab}>
              <Tabs value={tab} onChange={(v) => setTab(v ?? 'upload')} keepMounted={false}>
                <Tabs.List mb="md">
                  <Tabs.Tab value="upload" leftSection={<IconUpload size={15} />}>
                    Nạp mã nguồn
                  </Tabs.Tab>
                  <Tabs.Tab value="axes" leftSection={<IconAdjustments size={15} />}>
                    Chọn trục
                  </Tabs.Tab>
                  <Tabs.Tab value="conversation" leftSection={<IconMessages size={15} />}>
                    Hội thoại
                  </Tabs.Tab>
                  <Tabs.Tab value="chat" leftSection={<IconMessage size={15} />}>
                    Hỏi đáp
                  </Tabs.Tab>
                  <Tabs.Tab value="coverage" leftSection={<IconLayoutGrid size={15} />}>
                    Ba tầng mẫu số
                  </Tabs.Tab>
                  <Tabs.Tab value="grid" leftSection={<IconChartGridDots size={15} />}>
                    Lưới rủi ro
                  </Tabs.Tab>
                  <Tabs.Tab value="gates" leftSection={<IconShieldLock size={15} />}>
                    Chuỗi cổng
                  </Tabs.Tab>
                  <Tabs.Tab value="claims" leftSection={<IconListCheck size={15} />}>
                    Claim inspector
                  </Tabs.Tab>
                  <Tabs.Tab value="trace" leftSection={<IconRoute size={15} />}>
                    Trace viewer
                  </Tabs.Tab>
                  {/* Tab phụ — cố ý KHÔNG phải defaultValue (đặc tả lỗi 8). */}
                  <Tabs.Tab value="prompt-data" leftSection={<IconFileDescription size={15} />}>
                    Dữ liệu đã gửi
                  </Tabs.Tab>
                </Tabs.List>

                <Tabs.Panel value="upload">
                  <UploadPanel result={upload} onResult={handleUpload} />
                </Tabs.Panel>

                <Tabs.Panel value="axes">
                  {upload ? (
                    <AxisSelectionPanel
                      upload={upload}
                      confirmed={confirmedAxes}
                      onConfirm={setConfirmedAxes}
                    />
                  ) : (
                    <Text size="sm" c="dimmed">
                      Nạp một repo trước để engine đề xuất trục rủi ro.
                    </Text>
                  )}
                </Tabs.Panel>

                <Tabs.Panel value="conversation">
                  <ChatConversation />
                </Tabs.Panel>

                <Tabs.Panel value="chat">
                  <ChatPanel
                    question={question}
                    onQuestionChange={setQuestion}
                    onRun={run}
                    onStop={stop}
                    status={store.status}
                    answer={store.answer}
                    disabled={runDisabled}
                  />
                </Tabs.Panel>

                <Tabs.Panel value="coverage">
                  <CoverageTriptych
                    layers={coverageQuery.data ?? []}
                    loading={coverageQuery.isLoading}
                  />
                </Tabs.Panel>

                <Tabs.Panel value="grid">
                  <GridHeatmap cells={store.cells} />
                </Tabs.Panel>

                <Tabs.Panel value="gates">
                  <GateChain gates={store.gates} />
                </Tabs.Panel>

                <Tabs.Panel value="claims">
                  <ClaimInspector
                    claims={store.claims}
                    rejected={store.done?.rejected_claims ?? []}
                  />
                </Tabs.Panel>

                <Tabs.Panel value="trace">
                  <TraceViewer spans={store.spans} fallbackTraceId={store.done?.trace_id} />
                </Tabs.Panel>

                <Tabs.Panel value="prompt-data">
                  <PromptDataPanel
                    payload={promptQuery.data ?? null}
                    loading={promptQuery.isLoading}
                  />
                </Tabs.Panel>
              </Tabs>
              </ErrorBoundary>
            </Grid.Col>

            {/* Cột phải KHÔNG tự cuộn (bỏ scrollbar ngoài) — nó là flex-column
                bó sát viewport, để WarningFeed bên trong nuốt phần tràn bằng
                ScrollArea RIÊNG của nó. Nhờ vậy chỉ còn MỘT scrollbar: của Cảnh báo. */}
            <Grid.Col
              span={{ base: 12, lg: 3 }}
              style={{ maxHeight: 'calc(100dvh - 94px)', display: 'flex', flexDirection: 'column' }}
            >
              <Stack gap="md" style={{ flex: 1, minHeight: 0 }}>
                <RunControls
                  running={store.status === 'running'}
                  disabled={runDisabled}
                  onRun={run}
                  onStop={stop}
                  answerLength={store.answer.length}
                />
                {needsAxisSelection && (
                  <Alert color="yellow" variant="light" p="xs">
                    <Text size="xs">
                      Repo tải lên phải <b>chọn trục</b> trước khi phân tích — mở tab “Chọn
                      trục”, chốt 2–4 trục rồi chạy.
                    </Text>
                  </Alert>
                )}
                <StepProgress steps={store.steps} />
                <WarningFeed warnings={store.warnings} />
              </Stack>
            </Grid.Col>
          </Grid>
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}

/**
 * Nút chạy luôn trong tầm mắt ở cột phải, để chạy lại được từ bất kỳ tab nào
 * mà không phải quay về tab hỏi đáp.
 */
function RunControls({
  running,
  disabled,
  onRun,
  onStop,
  answerLength,
}: {
  running: boolean;
  disabled: boolean;
  onRun: () => void;
  onStop: () => void;
  answerLength: number;
}) {
  return (
    <Paper withBorder p="sm" radius="md">
      <Group gap="xs" wrap="nowrap">
        <Button
          size="xs"
          leftSection={<IconPlayerPlay size={15} />}
          disabled={disabled || running}
          onClick={onRun}
        >
          Chạy phân tích
        </Button>
        <Button
          size="xs"
          variant="light"
          color="red"
          leftSection={<IconPlayerStop size={15} />}
          disabled={!running}
          onClick={onStop}
        >
          Dừng
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mt={6}>
        {running
          ? `đang nhận token (${answerLength} ký tự)`
          : disabled
            ? 'chưa có mã nguồn — hãy chọn một repo mẫu'
            : 'sẵn sàng'}
      </Text>
    </Paper>
  );
}
