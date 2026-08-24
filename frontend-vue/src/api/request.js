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
    let message = '网络请求失败'
    if (err.response?.data?.detail) {
      message =
        typeof err.response.data.detail === 'string'
          ? err.response.data.detail
          : JSON.stringify(err.response.data.detail)
    } else if (err.response?.data?.message) {
      message = err.response.data.message
    } else if (err.message) {
      message = err.message
    }
    return Promise.reject(new Error(message))
  }
)

export default request
