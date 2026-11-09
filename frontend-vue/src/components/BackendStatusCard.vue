<script setup>
import { useAppStore } from '../stores/app'
import { Close, CircleCheckFilled, WarningFilled, Loading } from '@element-plus/icons-vue'

const store = useAppStore()
// 开发命令只在开发环境显示；生产/演示包给终端用户看启动命令只会造成困惑
const isDev = import.meta.env.DEV
</script>

<template>
  <!-- 右下角后端服务状态浮窗（App 全局 + 登录页共用，行为与数据来源不变） -->
  <transition name="bs-pop">
    <div
      v-if="!store.backendStatusDismissed"
      class="backend-status"
      :class="store.backendOnline ? 'online' : store.healthChecked ? 'offline' : 'checking'"
    >
      <div class="status-icon">
        <el-icon v-if="store.backendOnline"><CircleCheckFilled /></el-icon>
        <el-icon v-else-if="store.healthChecked"><WarningFilled /></el-icon>
        <el-icon v-else class="is-spin"><Loading /></el-icon>
      </div>
      <div class="status-meta">
        <div class="status-title">
          {{ store.backendOnline ? '后端服务在线' : store.healthChecked ? '服务暂时不可用' : '检查后端服务…' }}
        </div>
        <div class="status-desc">
          {{
            store.backendOnline
              ? '服务已连接'
              : store.healthChecked
                ? (isDev ? '请启动后端：python -m uvicorn app.main:app --reload' : '请稍后重试或联系管理员')
                : '正在检测服务状态…'
          }}
        </div>
      </div>
      <button
        class="status-close"
        type="button"
        aria-label="关闭"
        title="关闭"
        @click="store.dismissBackendStatus()"
      >
        <el-icon :size="12"><Close /></el-icon>
      </button>
    </div>
  </transition>
</template>

<style scoped>
.backend-status {
  position: fixed;
  bottom: 22px;
  right: 22px;
  z-index: 1000;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 30px 10px 10px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(10px);
  border-radius: 14px;
  border: 1px solid #eef0f6;
  box-shadow: 0 12px 32px rgba(31, 48, 92, 0.18);
  max-width: 320px;
}
.status-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  color: #fff;
  flex-shrink: 0;
}
.online .status-icon {
  background: linear-gradient(135deg, #2bd493, #18b87a);
  box-shadow: 0 6px 14px -6px rgba(24, 184, 122, 0.7);
}
.offline .status-icon {
  background: linear-gradient(135deg, #ff7a8a, #f5475d);
  box-shadow: 0 6px 14px -6px rgba(245, 71, 93, 0.7);
}
.checking .status-icon {
  background: linear-gradient(135deg, #ffc24b, #f5a623);
  box-shadow: 0 6px 14px -6px rgba(245, 166, 35, 0.7);
}
.is-spin { animation: bs-rotate 1.1s linear infinite; }
@keyframes bs-rotate { to { transform: rotate(360deg); } }

.status-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.status-title {
  font-size: 13px;
  font-weight: 600;
  color: #1f2a44;
}
.status-desc {
  font-size: 11px;
  color: #94a0b8;
  line-height: 1.5;
}
.status-close {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #c0c4cc;
  cursor: pointer;
  border-radius: 50%;
  padding: 0;
  transition: all 0.2s;
}
.status-close:hover {
  color: #f5475d;
  background: #fef0f2;
}
.bs-pop-enter-active,
.bs-pop-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.bs-pop-enter-from,
.bs-pop-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.96);
}
</style>
