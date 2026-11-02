import { getCurrentInstance, onBeforeUnmount } from 'vue'

/**
 * 只允许「最后一次请求」更新展示状态，并对读请求做真正的取消（AbortController）。
 *
 * 用法（读请求）：
 *   const req = useLatestRequest()
 *   async function load() {
 *     const { token, signal } = req.beginRead()
 *     const data = await api.xxx(params, { signal })
 *     if (!req.isLatest(token)) return   // 已被更新的请求取代或组件已卸载
 *     state.value = data
 *   }
 *
 * 用法（写请求）：
 *   const token = req.begin()            // 只做「最后响应」判定，不发取消信号
 *   await api.submit(...)
 *   if (!req.isLatest(token)) return
 *
 * 注意：
 * - 写操作（新增/删除/修改/提交答案）必须用 begin()：取消已发出的写请求会让
 *   前端以为失败、后端却已改完，属于数据不一致的来源。
 * - beginRead() 会真正中断上一次在途的读请求；被中断的请求会以
 *   CanceledError（code = 'ERR_CANCELED'）reject，调用方用 isAbortError 判断后静默返回。
 * - 组件卸载时既有请求结果失效，也会中断在途读请求，避免异步回调继续改已销毁组件的状态。
 */

/** 浏览器是否支持 AbortController（单测/极老环境降级为「只丢弃结果」） */
const CAN_ABORT = typeof AbortController === 'function'

export function useLatestRequest() {
  let sequence = 0
  let alive = true
  let controller = null

  function abortCurrent() {
    if (!controller) return
    const current = controller
    controller = null
    if (typeof current.abort === 'function') current.abort()
  }

  if (getCurrentInstance()) {
    onBeforeUnmount(() => {
      alive = false
      sequence += 1
      abortCurrent()
    })
  }

  /** 开始一次请求：只递增序号，不取消在途请求（写操作用） */
  function begin() {
    sequence += 1
    return sequence
  }

  /**
   * 开始一次读请求：先中断上一次在途读请求，再返回本次的 { token, signal }。
   * 同一批并发请求（Promise.all）共用一个 signal，再次发起时会被一起取消。
   */
  function beginRead() {
    abortCurrent()
    controller = CAN_ABORT ? new AbortController() : null
    sequence += 1
    return { token: sequence, signal: controller ? controller.signal : undefined }
  }

  function isLatest(token) {
    return alive && token === sequence
  }

  /** 让所有在途请求的结果失效并中断其网络请求（例如切换课程/资料时） */
  function invalidate() {
    sequence += 1
    abortCurrent()
  }

  return { begin, beginRead, isLatest, invalidate }
}

/**
 * 是否为「主动取消」导致的错误。
 * axios 抛 CanceledError（code = ERR_CANCELED），原生 fetch 抛 AbortError；
 * request.js 对这两类错误原样上抛、不再包装成「网络请求失败」。
 */
export function isAbortError(err) {
  if (!err) return false
  return err.code === 'ERR_CANCELED'
    || err.name === 'CanceledError'
    || err.name === 'AbortError'
}

