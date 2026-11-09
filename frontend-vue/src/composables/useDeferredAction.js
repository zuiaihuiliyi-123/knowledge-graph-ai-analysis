import { h, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'

/**
 * 延时执行 + 可撤销
 *
 * 删除类操作没法真的「改回来」（文档、成员、题目物理删除后无法重建），
 * 所以这里换一种做法：点击删除后**先不发请求**，只给一条带「撤销」的提示，
 * 过了 undo 窗口才真正提交。窗口内点「撤销」= 什么都没发生。
 *
 * 用法：
 *   const { schedule } = useDeferredAction()
 *   schedule({
 *     text: '已删除《xxx》',
 *     run: () => api.deleteDocument(id),      // 5 秒后才执行
 *     onUndo: () => restoreRow(),             // 撤销时把行放回去
 *   })
 *
 * 注意：组件卸载时会把待执行的任务**立即执行**（用户已经确认删除，
 * 只是来不及等倒计时；否则切页就会把这次删除悄悄丢掉）。
 */
export function useDeferredAction({ duration = 5000, undoText = '撤销' } = {}) {
  /** @type {Array<{timer: number, run: Function}>} */
  let pending = []

  function flush() {
    const jobs = pending
    pending = []
    jobs.forEach(({ timer, run }) => {
      clearTimeout(timer)
      Promise.resolve()
        .then(run)
        .catch((e) => ElMessage.error(e?.message || '操作失败'))
    })
  }

  onBeforeUnmount(flush)

  function schedule({ text, run, onUndo, undoLabel = undoText }) {
    let done = false

    const message = ElMessage({
      type: 'success',
      duration,
      showClose: false,
      message: h('span', { style: 'display:inline-flex;align-items:center;gap:10px' }, [
        h('span', String(text || '')),
        h(
          'button',
          {
            type: 'button',
            style:
              'border:none;background:transparent;color:#409eff;font-size:13px;font-weight:600;cursor:pointer;padding:0',
            onClick: () => {
              if (done) return
              done = true
              clearTimeout(timer)
              pending = pending.filter((job) => job.timer !== timer)
              message.close()
              try {
                onUndo && onUndo()
              } catch {
                /* 撤销回调失败不影响界面 */
              }
            },
          },
          undoLabel,
        ),
      ]),
    })

    const timer = setTimeout(() => {
      if (done) return
      done = true
      pending = pending.filter((job) => job.timer !== timer)
      Promise.resolve()
        .then(run)
        .catch((e) => ElMessage.error(e?.message || '操作失败'))
    }, duration)

    pending.push({ timer, run })
    return {
      cancel() {
        if (done) return
        done = true
        clearTimeout(timer)
        pending = pending.filter((job) => job.timer !== timer)
        message.close()
      },
    }
  }

  return { schedule, flush }
}
