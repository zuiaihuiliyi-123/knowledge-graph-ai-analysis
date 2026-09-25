<template>
  <div class="admin-users">
    <PageHeader title="用户管理" desc="平台全部账号的检索、资料查看与账号级治理">
      <template #extra>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 筛选栏 -->
    <el-card class="page-card" shadow="never">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索用户名 / 姓名 / 昵称 / 学号 / 工号 / 邮箱"
          clearable
          style="width: 320px"
          @keyup.enter="search"
          @clear="search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="query.role" placeholder="全部角色" clearable style="width: 130px" @change="search">
          <el-option label="管理员" value="admin" />
          <el-option label="教师" value="teacher" />
          <el-option label="学生" value="student" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px" @change="search">
          <el-option label="启用" value="active" />
          <el-option label="已禁用" value="disabled" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
        <span class="stats-text" style="margin-left: auto">共 {{ total }} 个账号</span>
      </div>
    </el-card>

    <!-- 列表 -->
    <el-card class="page-card" shadow="never">
      <el-table
        :data="rows"
        v-loading="loading"
        stripe
        row-key="user_id"
        @sort-change="onSort"
      >
        <el-table-column prop="username" label="用户名" min-width="130" sortable="custom">
          <template #default="{ row }">
            <div class="user-cell">
              <span class="uc-avatar" :class="row.role">{{ initial(row) }}</span>
              <div class="uc-text">
                <div class="uc-name">{{ row.display_name || row.real_name || row.nickname || row.username }}</div>
                <div class="uc-sub">{{ row.username }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="roleTagType(row.role)" effect="light">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="92">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_active ? 'success' : 'danger'" effect="plain">
              {{ row.is_active ? '启用' : '已禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="student_no" label="学号 / 工号" width="130">
          <template #default="{ row }">
            <span class="dim">{{ row.student_no || row.teacher_no || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="school" label="学校" min-width="130">
          <template #default="{ row }"><span class="dim">{{ row.school || '—' }}</span></template>
        </el-table-column>
        <el-table-column prop="created_at" label="注册时间" width="150" sortable="custom">
          <template #default="{ row }"><span class="dim">{{ fmtTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column prop="last_active" label="最近活跃" width="150" sortable="custom">
          <template #default="{ row }"><span class="dim">{{ fmtTime(row.last_active) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.user_id)">详情</el-button>
            <el-button
              link
              :type="row.is_active ? 'warning' : 'success'"
              @click="toggleActive(row)"
            >{{ row.is_active ? '禁用' : '启用' }}</el-button>
            <el-button link type="primary" @click="openReset(row)">重置密码</el-button>
          </template>
        </el-table-column>
        <template #empty><div class="empty-line">没有符合条件的账号</div></template>
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

    <!-- 用户详情抽屉 -->
    <el-drawer v-model="detailVisible" title="用户详情" size="600px" :destroy-on-close="true">
      <div v-if="detail" v-loading="detailLoading" class="detail-body">
        <div class="detail-head">
          <span class="uc-avatar lg" :class="detail.user.role">{{ initial(detail.user) }}</span>
          <div class="dh-text">
            <div class="dh-name">{{ detail.user.display_name || detail.user.real_name || detail.user.username }}</div>
            <div class="dh-tags">
              <el-tag size="small" :type="roleTagType(detail.user.role)">{{ roleLabel(detail.user.role) }}</el-tag>
              <el-tag size="small" :type="detail.user.is_active ? 'success' : 'danger'" effect="plain">
                {{ detail.user.is_active ? '启用' : '已禁用' }}
              </el-tag>
              <span class="dim">ID {{ detail.user.user_id }}</span>
            </div>
          </div>
        </div>

        <!-- 账号操作 -->
        <div class="detail-actions">
          <el-button size="small" @click="openEdit(detail.user)">编辑资料</el-button>
          <el-button size="small" :type="detail.user.is_active ? 'warning' : 'success'" @click="toggleActive(detail.user)">
            {{ detail.user.is_active ? '禁用账号' : '启用账号' }}
          </el-button>
          <el-button size="small" @click="openRole(detail.user)">修改角色</el-button>
          <el-button size="small" @click="openReset(detail.user)">重置密码</el-button>
          <el-button size="small" type="danger" plain @click="tryDelete(detail.user)">删除账号</el-button>
        </div>

        <el-divider content-position="left">账号信息</el-divider>
        <div class="kv-grid">
          <div class="kv"><span class="kv-k">用户名</span><span class="kv-v">{{ detail.user.username }}</span></div>
          <div class="kv"><span class="kv-k">邮箱</span><span class="kv-v">{{ detail.user.email || '—' }}</span></div>
          <div class="kv"><span class="kv-k">注册时间</span><span class="kv-v">{{ fmtTime(detail.user.created_at) }}</span></div>
          <div class="kv"><span class="kv-k">资料更新时间</span><span class="kv-v">{{ fmtTime(detail.user.updated_at) }}</span></div>
          <div class="kv"><span class="kv-k">密码</span><span class="kv-v dim">不可查看（系统只保存哈希）</span></div>
        </div>

        <el-divider content-position="left">个人资料</el-divider>
        <div class="kv-grid">
          <div class="kv"><span class="kv-k">真实姓名</span><span class="kv-v">{{ detail.user.real_name || '—' }}</span></div>
          <div class="kv"><span class="kv-k">昵称</span><span class="kv-v">{{ detail.user.nickname || '—' }}</span></div>
          <div class="kv"><span class="kv-k">性别</span><span class="kv-v">{{ genderLabel(detail.user.gender) }}</span></div>
          <div class="kv"><span class="kv-k">学校</span><span class="kv-v">{{ detail.user.school || '—' }}</span></div>
          <div class="kv"><span class="kv-k">学院</span><span class="kv-v">{{ detail.user.college || '—' }}</span></div>
          <template v-if="detail.user.role === 'student'">
            <div class="kv"><span class="kv-k">学号</span><span class="kv-v">{{ detail.user.student_no || '—' }}</span></div>
            <div class="kv"><span class="kv-k">专业</span><span class="kv-v">{{ detail.user.major || '—' }}</span></div>
            <div class="kv"><span class="kv-k">年级</span><span class="kv-v">{{ detail.user.grade || '—' }}</span></div>
            <div class="kv"><span class="kv-k">班级</span><span class="kv-v">{{ detail.user.class_name || '—' }}</span></div>
          </template>
          <template v-if="detail.user.role === 'teacher'">
            <div class="kv"><span class="kv-k">工号</span><span class="kv-v">{{ detail.user.teacher_no || '—' }}</span></div>
            <div class="kv"><span class="kv-k">职称</span><span class="kv-v">{{ detail.user.title || '—' }}</span></div>
            <div class="kv"><span class="kv-k">研究方向</span><span class="kv-v">{{ detail.user.research_area || '—' }}</span></div>
          </template>
          <div class="kv wide"><span class="kv-k">个人简介</span><span class="kv-v">{{ detail.user.bio || '—' }}</span></div>
        </div>

        <el-divider content-position="left">数据统计</el-divider>
        <div class="stat-grid">
          <div v-for="s in detailStats" :key="s.label" class="stat-cell">
            <div class="sc-value">{{ s.value }}</div>
            <div class="sc-label">{{ s.label }}</div>
          </div>
        </div>

        <el-divider content-position="left">所在课程（{{ (detail.courses || []).length }}）</el-divider>
        <div v-if="(detail.courses || []).length" class="course-list">
          <div v-for="c in detail.courses" :key="c.course_id" class="course-row">
            <span class="cr-name">{{ c.course_name }}</span>
            <el-tag size="small" effect="plain">{{ relRoleLabel(c.rel_role) }}</el-tag>
            <el-tag v-if="c.member_status !== 'approved'" size="small" type="warning" effect="plain">
              {{ memberStatusLabel(c.member_status) }}
            </el-tag>
            <el-tag v-if="c.governance_status !== 'normal'" size="small" type="info" effect="plain">
              {{ c.governance_status === 'hidden' ? '已下架' : '已归档' }}
            </el-tag>
          </div>
        </div>
        <div v-else class="empty-line">该账号未参与任何课程</div>

        <el-divider content-position="left">相关操作记录</el-divider>
        <div v-if="(detail.audit_logs || []).length" class="audit-list">
          <div v-for="a in detail.audit_logs" :key="a.log_id" class="audit-row">
            <span class="ar-action">{{ a.action }}</span>
            <span class="ar-detail">{{ a.detail || '—' }}</span>
            <span class="dim">{{ fmtTime(a.created_at) }}</span>
          </div>
        </div>
        <div v-else class="empty-line">暂无针对该账号的管理操作记录</div>
      </div>
    </el-drawer>

    <!-- 编辑资料 -->
    <el-dialog v-model="editVisible" title="编辑用户资料" width="520px" align-center>
      <el-form :model="editForm" label-width="88px">
        <el-form-item label="显示名">
          <el-input v-model="editForm.display_name" maxlength="50" placeholder="留空表示清空" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="editForm.email" maxlength="100" placeholder="留空表示清空" />
        </el-form-item>
        <el-divider content-position="left">个人资料</el-divider>
        <el-form-item label="真实姓名">
          <el-input v-model="editForm.real_name" maxlength="50" />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="editForm.nickname" maxlength="50" />
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="editForm.gender" clearable placeholder="未设置" style="width: 100%">
            <el-option label="男" value="male" />
            <el-option label="女" value="female" />
            <el-option label="其他" value="other" />
            <el-option label="不愿透露" value="unknown" />
          </el-select>
        </el-form-item>
        <el-form-item label="学校">
          <el-input v-model="editForm.school" maxlength="100" />
        </el-form-item>
        <el-form-item label="个人简介">
          <el-input v-model="editForm.bio" type="textarea" :rows="3" maxlength="200" show-word-limit />
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="管理员只能修改资料字段。角色、状态、密码请使用对应的独立操作（都会记入审计日志）。"
        />
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 修改角色 -->
    <el-dialog v-model="roleVisible" title="修改角色" width="420px" align-center>
      <p class="dlg-tip">
        将 <b>{{ acting?.username }}</b> 的角色从
        {{ roleLabel(acting?.role) }} 修改为：
      </p>
      <el-radio-group v-model="roleForm.role">
        <el-radio-button label="student">学生</el-radio-button>
        <el-radio-button label="teacher">教师</el-radio-button>
        <el-radio-button label="admin">管理员</el-radio-button>
      </el-radio-group>
      <el-alert
        class="dlg-alert"
        type="warning"
        :closable="false"
        show-icon
        title="角色决定该账号能进入哪些界面。改为管理员会获得全平台治理权限。"
      />
      <template #footer>
        <el-button @click="roleVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitRole">确认修改</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetVisible" title="重置密码" width="460px" align-center>
      <el-alert
        class="dlg-alert"
        type="info"
        :closable="false"
        show-icon
        title="系统不会显示任何原有密码。可留空由系统生成随机初始密码，生成结果只在提交后显示一次。"
      />
      <el-form label-width="88px" class="reset-form">
        <el-form-item label="新密码">
          <el-input
            v-model="resetForm.new_password"
            type="password"
            show-password
            placeholder="留空 = 由系统生成随机密码"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitReset">确认重置</el-button>
      </template>
    </el-dialog>

    <!-- 生成的初始密码（只显示一次） -->
    <el-dialog v-model="generatedVisible" title="密码已重置" width="440px" align-center>
      <p class="dlg-tip">
        请把下面的初始密码转告 <b>{{ generatedFor }}</b>。关闭本窗口后将无法再次查看。
      </p>
      <div class="pwd-box">{{ generatedPassword }}</div>
      <template #footer>
        <el-button type="primary" @click="generatedVisible = false">我已记录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import { api } from '../../api'

const loading = ref(false)
const submitting = ref(false)
const rows = ref([])
const total = ref(0)

const query = reactive({
  keyword: '', role: '', status: '',
  page: 1, page_size: 20, sort_by: 'user_id', sort_order: 'desc',
})

// 详情
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const acting = ref(null)

// 各弹窗
const editVisible = ref(false)
const roleVisible = ref(false)
const resetVisible = ref(false)
const generatedVisible = ref(false)
const generatedPassword = ref('')
const generatedFor = ref('')

const editForm = reactive({
  display_name: '', email: '', real_name: '', nickname: '', gender: '', school: '', bio: '',
})
const roleForm = reactive({ role: 'student' })
const resetForm = reactive({ new_password: '' })

const fmtTime = (t) => (t ? String(t).slice(0, 16) : '—')
const roleLabel = (r) => ({ admin: '管理员', teacher: '教师', student: '学生' }[r] || r || '—')
function roleTagType(r) {
  return { admin: 'primary', teacher: 'warning', student: 'success' }[r] || 'info'
}
const genderLabel = (g) =>
  ({ male: '男', female: '女', other: '其他', unknown: '不愿透露' }[g] || '—')
const relRoleLabel = (r) =>
  ({ owner: '创建者', teacher: '协作教师', student: '学生', assistant: '助教' }[r] || r)
const memberStatusLabel = (s) =>
  ({ pending: '待审核', rejected: '已拒绝', removed: '已移除', approved: '已通过' }[s] || s)

function initial(row) {
  const name = row?.display_name || row?.real_name || row?.nickname || row?.username || '?'
  return String(name).slice(0, 1).toUpperCase()
}

const detailStats = computed(() => {
  const s = detail.value?.stats || {}
  return [
    { label: '创建课程', value: s.owned_course_count ?? 0 },
    { label: '加入课程', value: s.joined_course_count ?? 0 },
    { label: '上传文档', value: s.document_count ?? 0 },
    { label: '学习记录', value: s.learning_record_count ?? 0 },
    { label: '收藏', value: s.favorite_count ?? 0 },
    { label: '答题记录', value: s.answer_count ?? 0 },
  ]
})

async function load() {
  loading.value = true
  try {
    const params = { ...query }
    // 空筛选值不发给后端（后端按 None 处理，但省掉无意义的 query 更干净）
    Object.keys(params).forEach((k) => {
      if (params[k] === '' || params[k] === null) delete params[k]
    })
    const data = await api.adminListUsers(params)
    rows.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error(e?.message || '加载用户列表失败')
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
  query.role = ''
  query.status = ''
  query.sort_by = 'user_id'
  query.sort_order = 'desc'
  search()
}

function onSort({ prop, order }) {
  if (!prop || !order) {
    query.sort_by = 'user_id'
    query.sort_order = 'desc'
  } else {
    query.sort_by = prop
    query.sort_order = order === 'ascending' ? 'asc' : 'desc'
  }
  search()
}

async function openDetail(userId) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.adminGetUser(userId)
  } catch (e) {
    ElMessage.error(e?.message || '加载用户详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail() {
  if (detailVisible.value && detail.value) await openDetail(detail.value.user.user_id)
}

// ---- 启用 / 禁用 ----
async function toggleActive(row) {
  const disable = !!row.is_active
  try {
    if (disable) {
      await ElMessageBox.confirm(
        `确定要禁用「${row.username}」吗？禁用后该账号无法登录，但历史学习数据会保留。`,
        '禁用账号', { type: 'warning' },
      )
    }
    const fn = disable ? api.adminDisableUser : api.adminEnableUser
    await fn(row.user_id)
    ElMessage.success(disable ? '账号已禁用' : '账号已启用')
    await load()
    await refreshDetail()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e?.message || '操作失败')
  }
}

// ---- 编辑资料 ----
function openEdit(user) {
  acting.value = user
  editForm.display_name = user.display_name || ''
  editForm.email = user.email || ''
  editForm.real_name = user.real_name || ''
  editForm.nickname = user.nickname || ''
  editForm.gender = user.gender || ''
  editForm.school = user.school || ''
  editForm.bio = user.bio || ''
  editVisible.value = true
}

async function submitEdit() {
  submitting.value = true
  try {
    await api.adminUpdateUser(acting.value.user_id, {
      display_name: editForm.display_name,
      email: editForm.email,
      profile: {
        real_name: editForm.real_name,
        nickname: editForm.nickname,
        gender: editForm.gender,
        school: editForm.school,
        bio: editForm.bio,
      },
    })
    ElMessage.success('资料已保存')
    editVisible.value = false
    await load()
    await refreshDetail()
  } catch (e) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

// ---- 修改角色 ----
function openRole(user) {
  acting.value = user
  roleForm.role = user.role
  roleVisible.value = true
}

async function submitRole() {
  submitting.value = true
  try {
    await api.adminSetUserRole(acting.value.user_id, roleForm.role)
    ElMessage.success('角色已修改')
    roleVisible.value = false
    await load()
    await refreshDetail()
  } catch (e) {
    ElMessage.error(e?.message || '修改角色失败')
  } finally {
    submitting.value = false
  }
}

// ---- 重置密码 ----
function openReset(user) {
  acting.value = user
  resetForm.new_password = ''
  resetVisible.value = true
}

async function submitReset() {
  submitting.value = true
  try {
    const data = await api.adminResetPassword(acting.value.user_id, resetForm.new_password || null)
    resetVisible.value = false
    if (data.generated && data.initial_password) {
      generatedPassword.value = data.initial_password
      generatedFor.value = data.username
      generatedVisible.value = true
    } else {
      ElMessage.success('密码已重置')
    }
    await refreshDetail()
  } catch (e) {
    ElMessage.error(e?.message || '重置密码失败')
  } finally {
    submitting.value = false
  }
}

// ---- 删除 ----
async function tryDelete(user) {
  try {
    await ElMessageBox.confirm(
      `确定要删除账号「${user.username}」吗？只有从未产生课程 / 文档 / 学习记录等数据的账号才能删除；`
      + `有数据的账号请改用「禁用」（可随时恢复）。`,
      '删除账号', { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' },
    )
    await api.adminDeleteUser(user.user_id)
    ElMessage.success('账号已删除')
    detailVisible.value = false
    await load()
  } catch (e) {
    if (e !== 'cancel') {
      // 后端会明确说明「仍有业务数据，请改用禁用」，原样展示给管理员
      ElMessage.error(e?.message || '删除失败')
    }
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar { width: 100%; }

.user-cell { display: flex; align-items: center; gap: 9px; }
.uc-avatar {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  font-size: 13px;
  flex-shrink: 0;
  background: linear-gradient(135deg, #5b8def, #6a5cf6);
}
.uc-avatar.teacher { background: linear-gradient(135deg, #f5a623, #f4794d); }
.uc-avatar.admin { background: linear-gradient(135deg, #8b5cf6, #5b8def); }
.uc-avatar.lg { width: 46px; height: 46px; border-radius: 13px; font-size: 19px; }
.uc-text { min-width: 0; }
.uc-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.uc-sub { font-size: 11.5px; color: var(--color-text-muted); }
.dim { color: var(--color-text-muted); font-size: 12.5px; }

.pager { display: flex; justify-content: flex-end; margin-top: 14px; }
.empty-line { padding: 22px 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }

/* ---- 详情抽屉 ---- */
.detail-body { padding: 0 4px 12px; }
.detail-head { display: flex; align-items: center; gap: 12px; }
.dh-name { font-size: 16px; font-weight: 700; color: var(--text-primary); }
.dh-tags { display: flex; align-items: center; gap: 6px; margin-top: 5px; }
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
.kv-v.dim { font-weight: 400; color: var(--color-text-muted); }

.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.stat-cell {
  padding: 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  text-align: center;
}
.sc-value {
  font-family: var(--font-family-number);
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}
.sc-label { margin-top: 2px; font-size: 11.5px; color: var(--color-text-secondary); }

.course-list { display: flex; flex-direction: column; gap: 6px; }
.course-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  font-size: 13px;
}
.cr-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.audit-list { display: flex; flex-direction: column; gap: 6px; }
.audit-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  font-size: 12.5px;
}
.ar-action { font-weight: 600; color: var(--text-primary); flex-shrink: 0; }
.ar-detail {
  flex: 1;
  min-width: 0;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 弹窗 ---- */
.dlg-tip { margin: 0 0 12px; font-size: 13px; color: var(--text-regular); }
.dlg-alert { margin-top: 12px; border-radius: var(--radius-sm); }
.reset-form { margin-top: 10px; }
.pwd-box {
  padding: 14px;
  text-align: center;
  font-family: var(--font-family-number);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--brand-600);
  background: var(--brand-50);
  border: 1px dashed var(--brand-300);
  border-radius: var(--radius-md);
  user-select: all;
}
</style>
