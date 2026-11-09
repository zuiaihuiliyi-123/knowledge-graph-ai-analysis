<template>
  <!-- 登录 / 注册页共用的左侧品牌区：动态知识网络背景 + 卖点与数据 -->
  <div class="brand-panel">
    <canvas ref="networkCanvasRef" class="network-canvas"></canvas>
    <div class="brand-mask"></div>
    <div class="brand-content">
      <div class="brand-head">
        <div class="brand-logo"><BrandMark :size="34" /></div>
        <div>
          <div class="brand-title">智育数据</div>
          <div class="brand-en">Knowledge Graph Learning Platform</div>
        </div>
      </div>

      <div class="brand-hero">
        <h1 class="hero-title">让每一份课程资料<br />长出<span class="kg-gradient-text-light">可导航的知识图谱</span></h1>
        <p class="hero-desc">
          基于 AIGC 自动抽取知识点与四类关系，融合知识图谱、智能问答、
          个性化学习路径与题库练习，构建「教—学—评—练」一体化智能学习平台。
        </p>
      </div>

      <div class="feature-grid">
        <div v-for="f in features" :key="f.title" class="feature-item">
          <div class="feature-icon"><el-icon :size="18"><component :is="f.icon" /></el-icon></div>
          <div>
            <div class="feature-title">{{ f.title }}</div>
            <div class="feature-desc">{{ f.desc }}</div>
          </div>
        </div>
      </div>

      <div class="brand-foot">
        <span v-for="s in stats" :key="s.label" class="foot-stat">
          <b>{{ s.value }}</b>{{ s.label }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Share, ChatDotRound, Guide, DataAnalysis } from '@element-plus/icons-vue'
import BrandMark from './BrandMark.vue'

const features = [
  { title: 'AIGC 图谱构建', desc: '多格式资料解析，自动抽取知识点与四类关系', icon: Share },
  { title: '智能问答答疑', desc: '基于图谱的可溯源问答，答案引用知识来源', icon: ChatDotRound },
  { title: '个性化学习路径', desc: '前置依赖分析，动态生成推荐学习顺序', icon: Guide },
  { title: '教学数据监测', desc: '班级进度、薄弱点与掌握度多维分析', icon: DataAnalysis },
]
const stats = [
  { value: '4', label: '类知识关系' },
  { value: '4', label: '类知识点' },
  { value: '4', label: '类文档格式' },
  { value: '3', label: '角色协同' },
]

/* ---------- 动态知识网络动画（轻量 canvas，无依赖） ---------- */
const networkCanvasRef = ref(null)
let rafId = null
let resizeHandler = null

function startNetwork() {
  const canvas = networkCanvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  let w = 0
  let h = 0
  const COLORS = ['#7aa2ff', '#8b7bff', '#4fd1c5', '#ffffff']
  let nodes = []

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    w = rect.width
    h = rect.height
    canvas.width = w * dpr
    canvas.height = h * dpr
    canvas.style.width = w + 'px'
    canvas.style.height = h + 'px'
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const count = Math.max(26, Math.min(54, Math.floor((w * h) / 16000)))
    nodes = Array.from({ length: count }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - .5) * .35,
      vy: (Math.random() - .5) * .35,
      r: Math.random() * 2.2 + 1.4,
      c: COLORS[Math.floor(Math.random() * COLORS.length)],
    }))
  }

  function tick() {
    ctx.clearRect(0, 0, w, h)
    // 连线
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]
        const b = nodes[j]
        const dx = a.x - b.x
        const dy = a.y - b.y
        const dist = Math.hypot(dx, dy)
        if (dist < 130) {
          ctx.strokeStyle = `rgba(150, 175, 255, ${0.16 * (1 - dist / 130)})`
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(b.x, b.y)
          ctx.stroke()
        }
      }
    }
    // 节点
    for (const n of nodes) {
      n.x += n.vx
      n.y += n.vy
      if (n.x < 0 || n.x > w) n.vx *= -1
      if (n.y < 0 || n.y > h) n.vy *= -1
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
      ctx.fillStyle = n.c
      ctx.globalAlpha = .8
      ctx.fill()
      ctx.globalAlpha = 1
    }
    rafId = requestAnimationFrame(tick)
  }

  resize()
  tick()
  resizeHandler = resize
  window.addEventListener('resize', resize)
}

onMounted(startNetwork)
onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
})
</script>

<style scoped>
.brand-panel {
  flex: 1.15;
  position: relative;
  overflow: hidden;
  background: linear-gradient(150deg, #141d46 0%, #1b2566 48%, #2a2470 100%);
  display: flex;
}
.network-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.brand-mask {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(80% 60% at 20% 15%, rgba(91,141,239,.28), transparent 60%),
    radial-gradient(70% 55% at 85% 90%, rgba(139,92,246,.26), transparent 60%),
    linear-gradient(180deg, rgba(10,15,40,.15), rgba(10,15,40,.45));
}
.brand-content {
  position: relative;
  z-index: 1;
  width: 100%;
  padding: 46px 52px 84px;
  display: flex;
  flex-direction: column;
  color: #fff;
}
.brand-head {
  display: flex;
  align-items: center;
  gap: 13px;
}
.brand-logo {
  width: 50px;
  height: 50px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #5b8def, #8b5cf6);
  box-shadow: 0 10px 24px -8px rgba(91,141,239,.8), inset 0 1px 0 rgba(255,255,255,.25);
}
.brand-title { font-size: 22px; font-weight: 700; letter-spacing: 2px; }
.brand-en { font-size: 11px; color: rgba(210,218,248,.6); letter-spacing: 1px; margin-top: 3px; }

.brand-hero { margin: auto 0; }
.hero-title {
  font-size: 34px;
  line-height: 1.4;
  font-weight: 700;
  margin: 0 0 18px;
  letter-spacing: .5px;
}
.kg-gradient-text-light {
  background: linear-gradient(90deg, #8fb0ff, #b79bff 60%, #5eead4);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.hero-desc {
  font-size: 14px;
  line-height: 1.9;
  color: rgba(214,221,248,.78);
  max-width: 460px;
  margin: 0;
}

.feature-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 28px;
}
.feature-item {
  display: flex;
  gap: 11px;
  padding: 13px 14px;
  border-radius: 13px;
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.09);
  backdrop-filter: blur(6px);
  transition: transform .25s ease, background .25s ease;
}
.feature-item:hover { transform: translateY(-3px); background: rgba(255,255,255,.1); }
.feature-icon {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(91,141,239,.85), rgba(139,92,246,.85));
  box-shadow: 0 6px 14px -6px rgba(91,141,239,.9);
}
.feature-title { font-size: 13.5px; font-weight: 600; margin-bottom: 3px; }
.feature-desc { font-size: 11.5px; color: rgba(210,218,248,.62); line-height: 1.5; }

.brand-foot {
  display: flex;
  gap: 26px;
  margin-top: 30px;
  padding-top: 20px;
  border-top: 1px solid rgba(255,255,255,.09);
}
.foot-stat { font-size: 12px; color: rgba(210,218,248,.6); }
.foot-stat b {
  font-size: 20px;
  color: #fff;
  margin-right: 5px;
  font-family: var(--font-family-number);
}

@media (max-width: 960px) {
  .brand-panel { display: none; }
}
</style>
