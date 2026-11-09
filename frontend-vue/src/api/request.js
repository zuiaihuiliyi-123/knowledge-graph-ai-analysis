import axios from 'axios'

/**
 * 统一请求实例
 * 后端响应存在两种风格：
 *  1. 统一包装（/api/v1/graph 等）：{ code: 0, message, data, timestamp }
 *  2. 裸返回（/api/courses、/api/qa 等）：直接返回业务对象
 * 拦截器自动解包：有 code 字段则校验 code===0 并返回 data，否则原样返回。
 */
const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 120000,
})

// 请求拦截器：自动携带 JWT（token 由 stores/app.js 写入 localStorage 的 kg_token）
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('kg_token')
    if (token) {
      config.headers = config.headers || {}
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (err) => Promise.reject(err)
)

request.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body && 'data' in body) {
      if (body.code === 0) return body.data
      return Promise.reject(new Error(body.message || '接口返回错误'))
    }
    return body
  },
  (err) => {
    // 主动取消（AbortController）：原样上抛，交由调用方按 isAbortError 静默忽略。
    // 不能走下面的统一包装——否则用户点「停止生成」或切课程时，被取消的请求会弹「网络请求失败」。
    if (axios.isCancel?.(err) || err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') {
      return Promise.reject(err)
    }
    // 401 未认证/登录过期：清除本地凭证并跳转登录页。
    // 不再「静默整页刷新」，而是：
    //   1) 记一个 sessionStorage 标记，登录页据此提示「登录已过期」；
    //   2) 把当前地址作为 redirect 带上，登录成功后回到原页面。
    // 仍用整页跳转（而不是 import router），避免 request ↔ router ↔ store 的循环依赖。
    if (err.response?.status === 401) {
      localStorage.removeItem('kg_token')
      localStorage.removeItem('kg_user')
      if (window.location.pathname !== '/login') {
        try {
          sessionStorage.setItem('kg_auth_expired', '1')
        } catch { /* 存储不可用时忽略 */ }
        const redirect = window.location.pathname + window.location.search + window.location.hash
        window.location.href = `/login?redirect=${encodeURIComponent(redirect)}`
      }
    }
    let message = '网络请求失败'
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail) && detail.length) {
      // FastAPI 默认 422 形状（{detail:[{loc,msg},...]}）：取第一条可读信息，
      // 不再把整个数组 JSON.stringify 后塞进 toast。
      const first = detail[0]
      const loc = Array.isArray(first?.loc)
        ? first.loc.filter((x) => x !== 'body').join('.')
        : ''
      message = loc ? `参数校验失败：「${loc}」${first?.msg || ''}` : (first?.msg || '参数校验失败')
    } else if (err.response?.data?.message) {
      message = err.response.data.message
    } else if (err.code === 'ECONNABORTED' || /timeout/i.test(err.message || '')) {
      message = '网络请求超时，请稍后重试'
    } else if (!err.response) {
      message = '网络异常，请检查网络后重试'
    } else if (err.message) {
      message = err.message
    }
    // 附带 HTTP 状态码：二进制响应（responseType: 'arraybuffer'，如文档在线阅读）
    // 的响应体不是 JSON，上面的 detail/message 提取拿不到内容，调用方需要靠状态码区分
    // 「无权限(403)」「文件已移除(404)」等情况。仅新增属性，不改变既有 message 语义。
    const wrapped = new Error(message)
    wrapped.status = err.response?.status
    return Promise.reject(wrapped)
  }
)

export default request
