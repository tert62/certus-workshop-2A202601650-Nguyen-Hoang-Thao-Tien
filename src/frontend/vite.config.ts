import { existsSync } from 'node:fs';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Đa trang có điều kiện: app chính luôn có; trang giảng viên (learning.html) chỉ
// thêm vào build khi file còn tồn tại. Bản build repo sinh viên loại learning.html
// đi, nên vòng này bỏ qua nó và build KHÔNG vỡ vì trỏ tới file vắng mặt.
const learningHtml = fileURLToPath(new URL('./learning.html', import.meta.url));
const input: Record<string, string> = {
  main: fileURLToPath(new URL('./index.html', import.meta.url)),
};
if (existsSync(learningHtml)) {
  input.learning = learningHtml;
}

// Backend FastAPI chạy ở 8000; khi VITE_USE_MOCK=0 thì proxy này là đường ra thật.
//
// Đích proxy đổi được bằng `VITE_API_TARGET` vì 8000 là một trong những cổng bị
// tranh chấp nhiều nhất trên máy lập trình viên. Ca đã gặp thật: cổng 8000 đang
// bị một `python -m http.server` và một container Docker của dự án khác giữ, nên
// uvicorn của CERTUS bind thất bại rồi TẮT LẶNG, còn UI vẫn mở lên bình thường và
// gửi mọi request /api vào ứng dụng lạ. Triệu chứng là "giao diện chạy nhưng
// không có dữ liệu" — không chỗ nào nói ra rằng nó đang nói chuyện với app khác.
//
// Đổi cổng backend mà không đổi được đích proxy thì phải sửa tệp này rồi nhớ
// hoàn nguyên, và cái phải-nhớ-hoàn-nguyên là thứ sớm muộn bị commit nhầm.
const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: { input },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
