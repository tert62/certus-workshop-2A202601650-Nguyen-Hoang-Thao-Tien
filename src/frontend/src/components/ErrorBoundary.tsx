/**
 * Ranh giới lỗi cho vùng nội dung tab.
 *
 * Trước đây UI KHÔNG có error boundary: một component tab ném lỗi khi render là
 * cả cây React bị gỡ và người dùng thấy MÀN HÌNH TRẮNG — mất luôn header, cột
 * cảnh báo, mọi tab khác. Một lỗi cục bộ ở một panel không được phép giết cả
 * ứng dụng, nên panel hỏng chỉ thay bằng một thông báo đọc được; các tab khác
 * và luồng chạy vẫn sống.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Alert, Code, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

interface Props {
  children: ReactNode;
  /** Đổi giá trị này (ví dụ theo tab đang mở) để thử render lại sau khi lỗi. */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props): void {
    // Mở lại đúng panel hỏng thì thử dựng lại; đổi sang tab khác cũng reset.
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // console.error có chủ đích: panel vỡ phải để lại dấu vết ở devtools, nếu
    // không thì ErrorBoundary nuốt luôn cả nguyên nhân.
    console.error('[CERTUS] panel render lỗi:', error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Alert
        color="red"
        variant="light"
        icon={<IconAlertTriangle size={18} />}
        title="Panel này gặp lỗi khi hiển thị"
      >
        <Stack gap="xs">
          <Text size="sm">
            Một panel hỏng, nhưng phần còn lại của ứng dụng vẫn chạy — chuyển sang tab khác
            hoặc chạy lại phân tích. Nguyên văn lỗi:
          </Text>
          <Code block>{error.message}</Code>
        </Stack>
      </Alert>
    );
  }
}
