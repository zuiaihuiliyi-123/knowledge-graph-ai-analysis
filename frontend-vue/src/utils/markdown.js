// 轻量 Markdown 渲染工具（用于 AI 回答展示）
// 设计：先把原文本整个转义（& < > " '），再按行解析常见 Markdown 语法，
// 因此原始 HTML 不会被执行，天然规避 XSS；链接仅放行 http/https/mailto/tel 与站内相对地址。

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// 仅允许安全协议，其余（如 javascript:）统一降级为 '#'，避免点击执行脚本
function safeHref(url) {
  const u = String(url).trim()
  if (/^(https?:|mailto:|tel:)/i.test(u)) return u
  if (/^[/#]/.test(u)) return u
  return '#'
}

// 行内语法：行内代码 > 链接 > 粗体 > 斜体
function inline(text) {
  let out = text
  const codes = []
  // 行内代码 `code`，先抽出占位，避免其内部内容被后续规则误处理
  out = out.replace(/`([^`]+)`/g, (m, c) => {
    codes.push(c)
    return '\u0000C' + (codes.length - 1) + '\u0000'
  })
  // 链接 [text](url)
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => {
    return '<a href="' + safeHref(u) + '" target="_blank" rel="noopener noreferrer">' + t + '</a>'
  })
  // 粗体 **text** / __text__
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/__([^_]+)__/g, '<strong>$1</strong>')
  // 斜体 *text* / _text_（避免吃掉列表符号：要求前方非 *，后方非 *）
  out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
  out = out.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, '$1<em>$2</em>')
  // 还原行内代码
  out = out.replace(/\u0000C(\d+)\u0000/g, (m, i) => '<code>' + codes[+i] + '</code>')
  return out
}

/**
 * 将 Markdown 文本渲染为 HTML 字符串（用于 v-html）。
 */
export function renderMarkdown(src) {
  if (src === null || src === undefined) return ''
  const text = escapeHtml(String(src)).replace(/\r\n?/g, '\n')
  const lines = text.split('\n')
  const n = lines.length

  let html = ''
  let i = 0
  let listType = null // 'ul' | 'ol'
  let inCode = false
  let codeBuf = []
  let paraBuf = []

  const flushPara = () => {
    if (!paraBuf.length) return
    html += '<p>' + paraBuf.map(inline).join('<br>') + '</p>'
    paraBuf = []
  }
  const closeList = () => {
    if (listType) {
      html += '</' + listType + '>'
      listType = null
    }
  }

  while (i < n) {
    const line = lines[i]

    // 代码块围栏 ```
    const fence = line.match(/^\s*```(.*)$/)
    if (fence) {
      flushPara()
      closeList()
      if (!inCode) {
        inCode = true
        codeBuf = []
      } else {
        html += '<pre><code>' + codeBuf.join('\n') + '</code></pre>'
        inCode = false
        codeBuf = []
      }
      i++
      continue
    }
    if (inCode) {
      codeBuf.push(line)
      i++
      continue
    }

    // 空行：段落分隔
    if (/^\s*$/.test(line)) {
      flushPara()
      closeList()
      i++
      continue
    }

    // 水平线 --- / *** / ___
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      flushPara()
      closeList()
      html += '<hr>'
      i++
      continue
    }

    // 标题 # ~ ######（展示层最多到 h4）
    const h = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/)
    if (h) {
      flushPara()
      closeList()
      const lvl = Math.min(h[1].length, 4)
      html += '<h' + lvl + '>' + inline(h[2].trim()) + '</h' + lvl + '>'
      i++
      continue
    }

    // 引用 >
    const q = line.match(/^\s*>\s?(.*)$/)
    if (q) {
      flushPara()
      closeList()
      html += '<blockquote>' + inline(q[1]) + '</blockquote>'
      i++
      continue
    }

    // 无序列表 - / * / +
    const ul = line.match(/^\s*[-*+]\s+(.*)$/)
    if (ul) {
      flushPara()
      if (listType !== 'ul') {
        closeList()
        html += '<ul>'
        listType = 'ul'
      }
      html += '<li>' + inline(ul[1]) + '</li>'
      i++
      continue
    }

    // 有序列表 1. / 1)
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (ol) {
      flushPara()
      if (listType !== 'ol') {
        closeList()
        html += '<ol>'
        listType = 'ol'
      }
      html += '<li>' + inline(ol[1]) + '</li>'
      i++
      continue
    }

    // 普通段落行
    closeList()
    paraBuf.push(line.trim())
    i++
  }

  flushPara()
  closeList()
  if (inCode && codeBuf.length) {
    html += '<pre><code>' + codeBuf.join('\n') + '</code></pre>'
  }
  return html
}
