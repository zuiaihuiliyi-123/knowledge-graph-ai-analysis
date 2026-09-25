<template>
  <div class="admin-courses">
    <PageHeader title="课程管理" desc="全平台课程总览与课程级治理（教师端的「我的课程」仅覆盖自己创建的课程）">
      <template #extra>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索课程名称"
          clearable
          style="width: 240px"
          @keyup.enter="search"
          @clear="search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select
          v-model="query.teacher_id"
          placeholder="全部教师"
          clearable
          filterable
          style="width: 190px"
          @change="search"
        >
          <el-option
            v-for="t in teachers"
            :key="t.user_id"
            :label="t.is_active ? t.name : `${t.name}（已禁用）`"
            :value="t.user_id"
          />
        </el-select>
        <el-select v-model="query.governance_status" placeholder="全部治理状态" clearable style="width: 150px" @change="search">
          <el-option label="正常" value="normal" />
          <el-option label="已下架" value="hidden" />
          <el-option label="已归档" value="archived" />
        </el-select>
        <el-select v-model="query.is_public" placeholder="全部可见性" clearable style="width: 130px" @change="search">
          <el-option label="公开" :value="1" />
          <el-option label="非公开" :value="0" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
        <span class="stats-text" style="margin-left: auto">共 {{ total }} 门课程</span>
      </div>
    </el-card>

    <el-card class="page-card" shadow="never">
      <el-table :data="rows" v-loading="loading" stripe row-key="course_id" @sort-change="onSort">
        <el-table-column prop="course_name" label="课程名称" min-width="220" sortable="custom">
          <template #default="{ row }">
            <div class="cname">{{ row.course_name }}</div>
            <div class="csub">
              <span v-if="row.course_code">课程码 {{ row.course_code }} · </span>
              <span>ID {{ row.course_id }}</span>
              <span v-if="row.category"> · {{ row.category }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="创建教师" min-width="150">
          <template #default="{ row }">
            <template v-if="row.teacher_id">
              <span>{{ row.teacher_name || '未知用户' }}</span>
              <el-tag v-if="row.teacher_active === 0" size="small" type="danger" effect="plain" class="ml4">已禁用</el-tag>
            </template>
            <el-tag v-else size="small" type="warning" effect="plain">无负责人</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="member_count" label="成员" width="90" sortable="custom">
          <template #default="{ row }">
            {{ row.member_count || 0 }}
            <el-tooltip v-if="row.pending_member_count" :content="`${row.pending_member_count} 条待审核申请`">
              <el-tag size="small" type="warning" effect="plain">{{ row.pending_member_count }} 待审</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档" width="76" sortable="custom">
          <template #default="{ row }">{{ row.document_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="知识节点" width="96">
          <template #default="{ row }">
            <span class="dim">{{ row.entity_count || 0 }} / {{ row.relation_count || 0 }}</span>
            <div class="csub">节点 / 关系</div>
          </template>
        </el-table-column>
        <el-table-column label="公开" width="86">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_public ? 'success' : 'info'" effect="plain">
              {{ row.is_public ? '公开' : '非公开' }}
            </el-tag>
          </template>
        </el-table-column>
        <!-- 业务状态与治理状态是两个正交维度，分两列展示，避免看成一件事 -->
        <el-table-column label="业务状态" width="104">
          <template #default="{ row }">
            <el-tag size="small" :type="bizTagType(row.status)" effect="plain">
              {{ bizLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="治理状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="govTagType(row.governance_status)" effect="light">
              {{ govLabel(row.governance_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="学生可见" width="96">
          <template #default="{ row }">
            <span :class="['vis-cell', visibleToStudents(row) ? 'on' : 'off']">
              {{ visibleToStudents(row) ? '可见' : '不可见' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="116" sortable="custom">
          <template #default="{ row }"><span class="dim">{{ fmtDate(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.course_id)">详情</el-button>
            <el-button link type="primary" @click="openGovernance(row)">治理</el-button>
          </template>
        </el-table-column>
        <template #empty><div class="empty-line">没有符合条件的课程</div></template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.page_size"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="load"
          @size-change="search"
        />
      </div>
    </el-card>

    <!-- 课程详情（只读） -->
    <el-drawer v-model="detailVisible" title="课程详情" size="680px" :destroy-on-close="true">
      <div v-if="detail" v-loading="detailLoading" class="detail-body">
        <div class="dh-name">{{ detail.course.course_name }}</div>
        <div class="dh-tags">
          <el-tag size="small" :type="bizTagType(detail.course.status)" effect="plain">
            业务状态：{{ bizLabel(detail.course.status) }}
          </el-tag>
          <el-tag size="small" :type="govTagType(detail.course.governance_status)">
            治理状态：{{ govLabel(detail.course.governance_status) }}
          </el-tag>
          <el-tag size="small" :type="detail.course.is_public ? 'success' : 'info'" effect="plain">
            {{ detail.course.is_public ? '公开' : '非公开' }}
          </el-tag>
          <el-tag v-if="detail.course.teacher_active === 0" size="small" type="danger" effect="plain">
            负责人已禁用
          </el-tag>
          <span class="dim">ID {{ detail.course.course_id }}</span>
        </div>

        <div class="detail-actions">
          <el-button size="small" @click="openGovernance(detail.course)">课程治理</el-button>
          <el-button size="small" @click="openTransfer(detail.course)">转移负责人</el-button>
          <el-button size="small" type="danger" plain @click="tryDelete(detail.course)">删除课程</el-button>
        </div>

        <el-alert
          class="ro-alert"
          type="info"
          :closable="false"
          show-icon
          title="管理员对课程只做治理与处置，不修改课程内容、不代教师管理成员、不动学生学习数据。"
        />

        <el-divider content-position="left">概览</el-divider>
        <div class="kv-grid">
          <div class="kv"><span class="kv-k">创建教师</span><span class="kv-v">{{ detail.course.teacher_name || '—' }}</span></div>
          <div class="kv"><span class="kv-k">课程码</span><span class="kv-v">{{ detail.course.course_code || '—' }}</span></div>
          <div class="kv"><span class="kv-k">加入方式</span><span class="kv-v">{{ joinModeLabel(detail.course.join_mode) }}</span></div>
          <div class="kv"><span class="kv-k">分类</span><span class="kv-v">{{ detail.course.category || '—' }}</span></div>
          <div class="kv"><span class="kv-k">创建时间</span><span class="kv-v">{{ fmtTime(detail.course.created_at) }}</span></div>
          <div class="kv"><span class="kv-k">最近更新</span><span class="kv-v">{{ fmtTime(detail.course.updated_at) }}</span></div>
          <div v-if="detail.course.archived_at" class="kv">
            <span class="kv-k">归档时间</span><span class="kv-v">{{ fmtTime(detail.course.archived_at) }}</span>
          </div>
          <div v-if="detail.course.governance_note" class="kv wide">
            <span class="kv-k">治理备注</span><span class="kv-v">{{ detail.course.governance_note }}</span>
          </div>
          <div v-if="detail.course.description" class="kv wide">
            <span class="kv-k">课程简介</span><span class="kv-v">{{ detail.course.description }}</span>
          </div>
        </div>

        <el-divider content-position="left">数据规模</el-divider>
        <div class="stat-grid">
          <div class="stat-cell">
            <div class="sc-value">{{ detail.course.member_count || 0 }}</div>
            <div class="sc-label">已通过成员</div>
          </div>
          <div class="stat-cell">
            <div class="sc-value">{{ detail.course.pending_member_count || 0 }}</div>
            <div class="sc-label">待审核申请</div>
          </div>
          <div class="stat-cell">
            <div class="sc-value">{{ detail.course.document_count || 0 }}</div>
            <div class="sc-label">文档</div>
          </div>
          <div class="stat-cell">
            <div class="sc-value">{{ graphAvailable ? detail.graph.node_count : '—' }}</div>
            <div class="sc-label">知识节点</div>
          </div>
          <div class="stat-cell">
            <div class="sc-value">{{ graphAvailable ? detail.graph.edge_count : '—' }}</div>
            <div class="sc-label">知识关系</div>
          </div>
        </div>
        <div v-if="!graphAvailable" class="warn-line">Neo4j 不可用，无法统计图谱数据（不是 0，是没测到）</div>

        <el-divider content-position="left">成员（{{ (detail.members || []).length }}）</el-divider>
        <el-table :data="detail.members || []" size="small" max-height="260">
          <el-table-column label="成员" min-width="130">
            <template #default="{ row }">
              <!-- name 由 MemberService 统一按「昵称 > 真名 > 显示名 > 用户名」生成 -->
              {{ row.name || row.username }}
              <div class="csub">{{ row.username }}</div>
            </template>
          </el-table-column>
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ memberRoleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'approved' ? 'success' : 'warning'" effect="plain">
                {{ memberStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <template #empty><div class="empty-line">暂无成员</div></template>
        </el-table>

        <el-divider content-position="left">文档（{{ (detail.documents || []).length }}）</el-divider>
        <el-table :data="detail.documents || []" size="small" max-height="260">
          <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
          <el-table-column prop="file_type" label="类型" width="70" />
          <el-table-column label="解析" width="96">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.parse_status)" effect="plain">{{ row.parse_status || '—' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="抽取" width="104">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.extract_status)" effect="plain">{{ row.extract_status || '—' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="节点/关系" width="96">
            <template #default="{ row }">
              <span class="dim">{{ row.entity_count || 0 }} / {{ row.relation_count || 0 }}</span>
            </template>
          </el-table-column>
          <template #empty><div class="empty-line">该课程暂无文档</div></template>
        </el-table>
      </div>
    </el-drawer>

    <!-- 课程治理 -->
    <el-dialog v-model="govVisible" title="课程治理" width="540px" align-center>
      <p class="dlg-tip">课程：<b>{{ acting?.course_name }}</b></p>

      <!-- 状态来源必须写清楚：管理员要能一眼看出「不能加入」是教师关的课，还是平台下架的 -->
      <div class="state-panel">
        <div class="sp-row">
          <span class="sp-label">业务状态</span>
          <el-tag size="small" :type="bizTagType(acting?.status)" effect="plain">
            {{ bizLabel(acting?.status) }}
          </el-tag>
          <span class="sp-src">来源：教师或管理员的「关闭 / 重新开放」设置</span>
        </div>
        <div class="sp-row">
          <span class="sp-label">治理状态</span>
          <el-tag size="small" :type="govTagType(acting?.governance_status)" effect="light">
            {{ govLabel(acting?.governance_status) }}
          </el-tag>
          <span class="sp-src">来源：平台治理（下架 / 归档）</span>
        </div>
        <div class="sp-row">
          <span class="sp-label">学生可见</span>
          <span :class="['vis-cell', visibleToStudents(acting) ? 'on' : 'off']">
            {{ visibleToStudents(acting) ? '可见（可发现、可加入）' : '不可见（无法发现、无法加入）' }}
          </span>
        </div>
      </div>

      <div v-for="g in govGroups" :key="g.key" class="gov-group">
        <div class="gg-title">{{ g.title }}</div>
        <div class="gg-hint">{{ g.hint }}</div>
        <div class="gov-actions">
          <div v-for="a in g.items" :key="a.action" class="gov-item" :class="{ disabled: a.disabled }">
            <div class="gi-text">
              <div class="gi-title">{{ a.label }}</div>
              <div class="gi-desc">{{ a.desc }}</div>
            </div>
            <el-button size="small" :type="a.type" :disabled="a.disabled" :loading="submitting" @click="submitGovernance(a)">
              {{ a.label }}
            </el-button>
          </div>
        </div>
      </div>
      <el-form label-width="70px" class="gov-note">
        <el-form-item label="备注">
          <el-input
            v-model="govForm.note"
            type="textarea"
            :rows="2"
            maxlength="200"
            show-word-limit
            placeholder="选填，仅对上方「平台治理」动作生效（写入课程治理备注）"
          />
          <div class="gg-hint">「关闭 / 重新开放」属于业务动作，不写治理备注；两者都会被记入审计日志。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="govVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 转移负责人 -->
    <el-dialog v-model="transferVisible" title="转移课程负责人" width="460px" align-center>
      <p class="dlg-tip">
        把课程 <b>{{ acting?.course_name }}</b> 的负责人从
        {{ acting?.teacher_name || '（无）' }} 转移给：
      </p>
      <el-select v-model="transferForm.teacher_id" filterable placeholder="选择接收的教师" style="width: 100%">
        <el-option
          v-for="t in transferCandidates"
          :key="t.user_id"
          :label="`${t.name}（${t.username}）`"
          :value="t.user_id"
        />
      </el-select>
      <el-alert
        class="dlg-alert"
        type="info"
        :closable="false"
        show-icon
        title="原负责人会保留为协作教师，不会丢失对该课程的访问。只能转移给状态正常的教师账号。"
      />
      <template #footer>
        <el-button @click="transferVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!transferForm.teacher_id" @click="submitTransfer">
          确认转移
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import { api } from '../../api'
import {
  bizLabel, bizTagType, govLabel, govTagType, visibleToStudents,
} from '../../utils/courseGovernance'

const route = useRoute()

const loading = ref(false)
const submitting = ref(false)
const rows = ref([])
const total = ref(0)
const teachers = ref([])

const query = reactive({
  keyword: '', teacher_id: null, governance_status: '', is_public: null,
  page: 1, page_size: 20, sort_by: 'course_id', sort_order: 'desc',
})

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const acting = ref(null)

const govVisible = ref(false)
const transferVisible = ref(false)
const govForm = reactive({ note: '' })
const transferForm = reactive({ teacher_id: null })

const fmtTime = (t) => (t ? String(t).slice(0, 16) : '—')
const fmtDate = (t) => (t ? String(t).slice(0, 10) : '—')
// 业务状态 / 治理状态 / 学生可见性的展示口径统一放在 utils/courseGovernance.js：
// 它同时被「课程管理」与「课程治理」两个页面使用，且与后端 course_is_visible 同源。
// （此前两个页面各自写了一份，语义调整时会漂移——这正是本次修复的问题之一。）
const joinModeLabel = (m) =>
  ({ open: '自由加入', approval: '需审核', invite: '仅邀请', code: '加课码' }[m] || m || '—')
const memberRoleLabel = (r) =>
  ({ owner: '创建者', teacher: '协作教师', student: '学生', assistant: '助教' }[r] || r || '—')
const memberStatusLabel = (s) =>
  ({ pending: '待审核', approved: '已通过', rejected: '已拒绝', removed: '已移除' }[s] || s || '—')
const statusTag = (s) =>
  ({ COMPLETED: 'success', PARSED: 'success', PARSING: 'warning', EXTRACTING: 'warning', FAILED: 'danger' }[s] || 'info')

const graphAvailable = computed(() => !!detail.value?.graph?.available)

// 治理动作按当前状态启用/禁用，避免发出必然失败或重复的请求。
// 分两组：治理维度只改治理状态，业务维度只改课程的业务状态，互不覆盖。
const govActions = computed(() => {
  const gs = acting.value?.governance_status || 'normal'
  const biz = acting.value?.status === 0 ? 0 : 1
  return [
    // ---- 第一组：平台治理（只写 governance_status，不动业务状态） ----
    {
      group: 'governance',
      action: 'hide', label: '下架课程', type: 'warning',
      desc: '学生无法再发现 / 申请 / 用加课码或邀请加入；课程的业务状态与全部数据原样保留',
      disabled: gs === 'hidden' || gs === 'archived',
    },
    {
      group: 'governance',
      action: 'restore', label: '撤销下架', type: 'primary',
      desc: '治理状态回到「正常」；课程的业务状态恢复成下架前的样子，不会被强行打开',
      disabled: gs !== 'hidden',
    },
    {
      group: 'governance',
      action: 'archive', label: '归档课程', type: 'info',
      desc: '冻结归档（可见性一并收回），保留数据供追溯',
      disabled: gs === 'archived',
    },
    {
      group: 'governance',
      action: 'unarchive', label: '取消归档', type: 'primary',
      desc: '从归档状态回到「正常」，业务状态同样原样保留',
      disabled: gs !== 'archived',
    },
    // ---- 第二组：课程业务状态（只写 status，不动治理状态） ----
    {
      group: 'business',
      action: 'close', label: '关闭课程', type: 'danger',
      desc: '把课程的业务状态置为「已关闭」——相当于管理员代教师停课',
      disabled: biz === 0 || gs !== 'normal',
    },
    {
      group: 'business',
      action: 'reopen', label: '重新开放', type: 'success',
      desc: '把课程的业务状态置回「开放」',
      disabled: biz === 1 || gs !== 'normal',
    },
  ]
})

const govGroups = computed(() => [
  {
    key: 'governance',
    title: '平台治理',
    hint: '只改变治理状态，课程的业务状态由教师（或下面的业务动作）决定，不会被覆盖',
    items: govActions.value.filter((a) => a.group === 'governance'),
  },
  {
    key: 'business',
    title: '课程业务状态',
    hint: '只改变课程自身的开放 / 关闭，不影响平台治理状态',
    items: govActions.value.filter((a) => a.group === 'business'),
  },
])

const transferCandidates = computed(() =>
  teachers.value.filter((t) => t.is_active && t.user_id !== acting.value?.teacher_id))

async function loadOptions() {
  try {
    const data = await api.adminOptions()
    teachers.value = data.teachers || []
  } catch {
    // 教师下拉失败不阻断列表加载（筛选仍可用关键词）
  }
}

async function load() {
  loading.value = true
  try {
    const params = { ...query }
    Object.keys(params).forEach((k) => {
      if (params[k] === '' || params[k] === null) delete params[k]
    })
    const data = await api.adminListCourses(params)
    rows.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error(e?.message || '加载课程列表失败')
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
  query.teacher_id = null
  query.governance_status = ''
  query.is_public = null
  query.sort_by = 'course_id'
  query.sort_order = 'desc'
  search()
}

function onSort({ prop, order }) {
  if (!prop || !order) {
    query.sort_by = 'course_id'
    query.sort_order = 'desc'
  } else {
    query.sort_by = prop
    query.sort_order = order === 'ascending' ? 'asc' : 'desc'
  }
  search()
}

async function openDetail(courseId) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.adminGetCourse(courseId)
  } catch (e) {
    ElMessage.error(e?.message || '加载课程详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail() {
  if (detailVisible.value && detail.value) await openDetail(detail.value.course.course_id)
}

// ---- 治理 ----
function openGovernance(course) {
  acting.value = course
  govForm.note = ''
  govVisible.value = true
}

async function submitGovernance(a) {
  submitting.value = true
  try {
    // 只有治理动作写治理备注；业务动作（关闭 / 重新开放）后端会忽略备注
    const note = a.group === 'governance' ? (govForm.note || null) : null
    await api.adminCourseGovernance(acting.value.course_id, a.action, note)
    ElMessage.success(`${a.label}成功`)
    govVisible.value = false
    await load()
    await refreshDetail()
  } catch (e) {
    ElMessage.error(e?.message || '治理操作失败')
  } finally {
    submitting.value = false
  }
}

// ---- 转移 ----
function openTransfer(course) {
  acting.value = course
  transferForm.teacher_id = null
  transferVisible.value = true
}

async function submitTransfer() {
  submitting.value = true
  try {
    await api.adminTransferCourse(acting.value.course_id, transferForm.teacher_id)
    ElMessage.success('课程负责人已转移')
    transferVisible.value = false
    await load()
    await refreshDetail()
  } catch (e) {
    ElMessage.error(e?.message || '转移失败')
  } finally {
    submitting.value = false
  }
}

// ---- 删除 ----
async function tryDelete(course) {
  try {
    await ElMessageBox.confirm(
      `删除课程「${course.course_name}」会一并清理其成员、文档、知识图谱与学习数据，且不可恢复。`
      + `若只是想让课程暂时消失，请改用「下架课程」。`,
      '删除课程', { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' },
    )
    await api.adminDeleteCourse(course.course_id)
    ElMessage.success('课程已删除')
    detailVisible.value = false
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e?.message || '删除课程失败')
  }
}

onMounted(async () => {
  // 支持从课程治理页带筛选条件跳转过来（如「查看全部已下架课程」）
  const gs = route.query.governance_status
  if (typeof gs === 'string' && ['normal', 'hidden', 'archived'].includes(gs)) {
    query.governance_status = gs
  }
  await loadOptions()
  await load()
})
</script>

<style scoped>
.toolbar { width: 100%; }

.cname {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.csub { font-size: 11.5px; color: var(--color-text-muted); margin-top: 1px; }
.dim { color: var(--color-text-muted); font-size: 12.5px; }
.ml4 { margin-left: 4px; }

.pager { display: flex; justify-content: flex-end; margin-top: 14px; }
.empty-line { padding: 22px 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }
.warn-line {
  margin-top: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: #fff8e8;
  border: 1px solid #ffe2b0;
  color: #a8700f;
  font-size: 12.5px;
}

/* ---- 详情 ---- */
.detail-body { padding: 0 4px 12px; }
.dh-name { font-size: 16px; font-weight: 700; color: var(--text-primary); }
.dh-tags { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}
.detail-actions .el-button + .el-button { margin-left: 0; }
.ro-alert { margin-top: 12px; border-radius: var(--radius-sm); }

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

.stat-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.stat-cell { padding: 10px 6px; border-radius: var(--radius-sm); background: var(--bg-soft); text-align: center; }
.sc-value {
  font-family: var(--font-family-number);
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}
.sc-label { margin-top: 2px; font-size: 11.5px; color: var(--color-text-secondary); }

/* ---- 治理弹窗 ---- */
.dlg-tip { margin: 0 0 12px; font-size: 13px; color: var(--text-regular); }

/* 状态来源面板：业务状态 / 治理状态 / 学生可见 三行，明确标注每一维是谁设置的 */
.state-panel {
  padding: 10px 12px;
  margin-bottom: 14px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
}
.sp-row { display: flex; align-items: center; gap: 8px; }
.sp-row + .sp-row { margin-top: 7px; }
.sp-label { flex: 0 0 62px; font-size: 12.5px; color: var(--color-text-secondary); }
.sp-src { font-size: 11.5px; color: var(--color-text-secondary); }
.vis-cell { font-size: 12.5px; font-weight: 600; }
.vis-cell.on { color: var(--brand-600, #2f6fed); }
.vis-cell.off { color: var(--color-danger, #d9534f); }

.gov-group + .gov-group { margin-top: 16px; }
.gg-title {
  font-size: 13px; font-weight: 600; color: var(--text-primary);
}
.gg-hint { font-size: 11.5px; color: var(--color-text-secondary); margin: 2px 0 8px; }
.gov-actions { display: flex; flex-direction: column; gap: 8px; }
.gov-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
}
.gov-item.disabled { opacity: .55; }
.gi-text { flex: 1; min-width: 0; }
.gi-title { font-size: 13.5px; font-weight: 600; color: var(--text-primary); }
.gi-desc { font-size: 11.5px; color: var(--color-text-secondary); margin-top: 2px; }
.gov-note { margin-top: 14px; }
.dlg-alert { margin-top: 12px; border-radius: var(--radius-sm); }

@media (max-width: 1280px) {
  .stat-grid { grid-template-columns: repeat(3, 1fr); }
}
</style>
