<template>
  <div class="admin-audit">
    <PageHeader title="审计日志" desc="管理员操作只追加写入，不提供修改与删除入口">
      <template #extra>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索操作详情 / 操作人 / 目标 ID / 动作"
          clearable
          style="width: 280px"
          @keyup.enter="search"
          @clear="search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select
          v-model="query.action"
          placeholder="全部操作类型"
          clearable
          filterable
          style="width: 190px"
          @change="search"
        >
          <el-option
            v-for="a in actions"
            :key="a"
            :label="actionLabel(a)"
            :value="a"
          />
        </el-select>
        <el-select v-model="query.target_type" placeholder="全部目标类型" clearable style="width: 150px" @change="search">
          <el-option v-for="t in targetTypes" :key="t" :label="targetLabel(t)" :value="t" />
        </el-select>
        <el-select v-model="query.result" placeholder="全部结果" clearable style="width: 120px" @change="search">
          <el-option label="成功" value="success" />
          <el-option label="失败" value="failure" />
        </el-select>
        <el-date-picker
          v-model="range"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 360px"
          @change="search"
        />
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <div class="toolbar-sub">
        <span class="stats-text">共 {{ total }} 条记录</span>
        <el-select
          v-model="query.operator_id"
          placeholder="按操作人筛选"
          clearable
          filterable
          size="small"
          style="width: 190px"
          @change="search"
        >
          <el-option
            v-for="t in admins"
            :key="t.user_id"
            :label="`${t.name}（${t.username}）`"
            :value="t.user_id"
          />
        </el-select>
      </div>

      <el-table :data="rows" v-loading="loading" stripe row-key="log_id">
        <el-table-column prop="created_at" label="时间" width="150">
          <template #default="{ row }"><span class="dim">{{ fmtTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作人" width="160">
          <template #default="{ row }">
            <div class="op-name">{{ row.operator_name || `用户 ${row.operator_id}` }}</div>
            <div class="op-role">{{ roleLabel(row.operator_role) }} · ID {{ row.operator_id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170">
          <template #default="{ row }">
            <el-tag size="small" :type="actionTag(row.action)" effect="light">
              {{ actionLabel(row.action) }}
            </el-tag>
            <div class="op-role">{{ row.action }}</div>
          </template>
        </el-table-column>
        <el-table-column label="目标" width="130">
          <template #default="{ row }">
            <template v-if="row.target_type">
              <span>{{ targetLabel(row.target_type) }}</span>
              <div class="op-role">ID {{ row.target_id ?? '—' }}</div>
            </template>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="86">
          <template #default="{ row }">
            <el-tag size="small" :type="row.result === 'failure' ? 'danger' : 'success'" effect="plain">
              {{ row.result === 'failure' ? '失败' : '成功' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="详情" min-width="260">
          <template #default="{ row }">
            <span class="detail-text">{{ row.detail || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="140">
          <template #default="{ row }">
            <span class="dim">{{ row.ip || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="" width="70" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty><div class="empty-line">没有符合条件的审计记录</div></template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.page_size"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="load"
          @size-change="search"
        />
      </div>
    </el-card>

    <el-drawer v-model="detailVisible" title="审计记录详情" size="520px" :destroy-on-close="true">
      <div v-if="acting" class="detail-body">
        <div class="dh-name">{{ actionLabel(acting.action) }}</div>
        <div class="dh-tags">
          <el-tag size="small" :type="acting.result === 'failure' ? 'danger' : 'success'" effect="light">
            {{ acting.result === 'failure' ? '失败' : '成功' }}
          </el-tag>
          <span class="dim">{{ acting.action }}</span>
        </div>

        <el-divider content-position="left">记录信息</el-divider>
        <div class="kv-grid">
          <div class="kv"><span class="kv-k">日志 ID</span><span class="kv-v">{{ acting.log_id }}</span></div>
          <div class="kv"><span class="kv-k">时间</span><span class="kv-v">{{ fmtTime(acting.created_at) }}</span></div>
          <div class="kv">
            <span class="kv-k">操作人</span>
            <span class="kv-v">{{ acting.operator_name || `用户 ${acting.operator_id}` }}</span>
          </div>
          <div class="kv"><span class="kv-k">操作人角色</span><span class="kv-v">{{ roleLabel(acting.operator_role) }}</span></div>
          <div class="kv"><span class="kv-k">目标类型</span><span class="kv-v">{{ targetLabel(acting.target_type) || '—' }}</span></div>
          <div class="kv"><span class="kv-k">目标 ID</span><span class="kv-v">{{ acting.target_id ?? '—' }}</span></div>
          <div class="kv"><span class="kv-k">来源 IP</span><span class="kv-v">{{ acting.ip || '—' }}</span></div>
          <div class="kv wide"><span class="kv-k">User-Agent</span><span class="kv-v">{{ acting.user_agent || '—' }}</span></div>
        </div>

        <el-divider content-position="left">操作详情</el-divider>
        <div class="detail-box">{{ acting.detail || '（无详情）' }}</div>

        <el-alert
          class="dlg-alert"
          type="info"
          :closable="false"
          show-icon
          title="审计日志只记录管理动作本身。涉及密码的操作只记录「已重置」，不记录也不展示任何密码内容。"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import { api } from '../../api'

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const actions = ref([])
const targetTypes = ref([])
const labels = ref({})
const admins = ref([])
const range = ref(null)

const detailVisible = ref(false)
const acting = ref(null)

const query = reactive({
  keyword: '', action: '', target_type: '', result: '',
  operator_id: null, page: 1, page_size: 20,
})

const fmtTime = (t) => (t ? String(t).slice(0, 19) : '—')
const roleLabel = (r) => ({ admin: '管理员', teacher: '教师', student: '学生' }[r] || r || '—')
const targetLabel = (t) =>
  ({ user: '用户', course: '课程', document: '文档', settings: '平台设置' }[t] || t || '')

// 动作中文名由后端下发（ACTION_LABELS），避免前后端各维护一份映射而逐渐不同步
function actionLabel(a) {
  return labels.value[a] || a
}

function actionTag(a) {
  if (!a) return 'info'
  if (a.includes('login')) return 'primary'
  if (a.includes('delete')) return 'danger'
  if (a.includes('disable') || a.includes('hide') || a.includes('close')) return 'warning'
  if (a.includes('enable') || a.includes('restore') || a.includes('reopen')) return 'success'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const params = { ...query }
    if (range.value?.length === 2) {
      params.start_time = range.value[0]
      params.end_time = range.value[1]
    }
    Object.keys(params).forEach((k) => {
      if (params[k] === '' || params[k] === null) delete params[k]
    })
    const data = await api.adminAuditLogs(params)
    rows.value = data.items || []
    total.value = data.total || 0
    actions.value = data.actions || []
    targetTypes.value = data.target_types || []
    labels.value = data.action_labels || {}
  } catch (e) {
    ElMessage.error(e?.message || '加载审计日志失败')
  } finally {
    loading.value = false
  }
}

function search() {
  query.page = 1
  load()
}

function reset() {
  query.keyword = ''
  query.action = ''
  query.target_type = ''
  query.result = ''
  query.operator_id = null
  range.value = null
  search()
}

function openDetail(row) {
  acting.value = row
  detailVisible.value = true
}

async function loadAdmins() {
  try {
    const data = await api.adminListUsers({ role: 'admin', page: 1, page_size: 100 })
    admins.value = (data.items || []).map((u) => ({
      user_id: u.user_id,
      username: u.username,
      name: u.display_name || u.real_name || u.username,
    }))
  } catch {
    // 操作人下拉失败不影响日志查询本身
  }
}

onMounted(async () => {
  await Promise.all([load(), loadAdmins()])
})
</script>

<style scoped>
.toolbar { width: 100%; }
.toolbar-sub {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 10px 0 6px;
}
.toolbar-sub .stats-text { flex: 1; }

.op-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.op-role { font-size: 11.5px; color: var(--color-text-muted); margin-top: 1px; }
.dim { color: var(--color-text-muted); font-size: 12.5px; }
.detail-text {
  font-size: 12.5px;
  color: var(--text-regular);
  line-height: 1.5;
}

.pager { display: flex; justify-content: flex-end; margin-top: 14px; }
.empty-line { padding: 22px 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }

/* ---- 详情 ---- */
.detail-body { padding: 0 4px 12px; }
.dh-name { font-size: 16px; font-weight: 700; color: var(--text-primary); }
.dh-tags { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }

.kv-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.kv {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  font-size: 13px;
  min-width: 0;
}
.kv.wide { grid-column: 1 / -1; }
.kv-k { color: var(--color-text-secondary); font-size: 12.5px; flex-shrink: 0; }
.kv-v {
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kv.wide .kv-v { white-space: normal; word-break: break-all; }

.detail-box {
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-regular);
  word-break: break-all;
}
.dlg-alert { margin-top: 14px; border-radius: var(--radius-sm); }

@media (max-width: 1280px) {
  .toolbar { flex-wrap: wrap; gap: 8px; }
}
</style>
