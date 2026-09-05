<template>
  <div class="cds">
    <el-form label-width="80px" @submit.prevent>
      <el-form-item label="课程" required>
        <el-select
          v-model="courseId"
          filterable
          placeholder="选择课程"
          style="width: 100%"
          @change="onCourseChange"
        >
          <el-option
            v-for="c in courseList"
            :key="c.course_id"
            :label="c.course_name"
            :value="String(c.course_id)"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="学习资料" required>
        <el-select
          v-model="documentId"
          filterable
          placeholder="先选择课程"
          style="width: 100%"
          :disabled="!courseId"
          :loading="docLoading"
        >
          <el-option
            v-for="d in documentList"
            :key="d.doc_id"
            :label="d.file_name"
            :value="String(d.doc_id)"
          >
            <div class="doc-option">
              <span class="doc-name">{{ d.file_name }}</span>
              <el-tag size="small" effect="plain">{{ d.file_type }}</el-tag>
              <span class="doc-meta">
                {{ parseStatusText(d.parse_status) }} · {{ d.entity_count }} 知识点 · {{ d.relation_count }} 关系
              </span>
            </div>
          </el-option>
        </el-select>
        <div v-if="courseId && !docLoading && documentList.length === 0" class="cds-hint">
          该课程暂无文档，请先在教师端上传
        </div>
        <div v-else-if="courseId && !docLoading && documentList.length === 1" class="cds-hint cds-single">
          <el-button text type="primary" @click="continueSingle">
            继续学习「{{ documentList[0].file_name }}」
          </el-button>
        </div>
      </el-form-item>
    </el-form>

    <div class="cds-footer">
      <el-button @click="emit('cancel')">取消</el-button>
      <el-button type="primary" :disabled="!courseId || !documentId" @click="confirm">确定</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const props = defineProps({
  initialCourseId: { type: [String, Number], default: null },
  initialDocumentId: { type: [String, Number], default: null },
})
const emit = defineEmits(['confirm', 'cancel'])
const store = useAppStore()

const courseId = ref(props.initialCourseId ? String(props.initialCourseId) : '')
const documentId = ref(props.initialDocumentId ? String(props.initialDocumentId) : '')
const documentList = ref([])
const docLoading = ref(false)

const courseList = computed(() => store.courses)

function parseStatusText(s) {
  const map = { UPLOADED: '待解析', PARSING: '解析中', PARSED: '已解析', FAILED: '解析失败' }
  return map[s] || s || ''
}

// 切换课程：必须清空已选文档（禁止沿用上一课程的文档）
async function onCourseChange(cid) {
  documentId.value = ''
  documentList.value = []
  if (!cid) return
  docLoading.value = true
  try {
    documentList.value = await api.getDocuments(cid)
  } catch (e) {
    documentList.value = []
  } finally {
    docLoading.value = false
  }
}

function confirm() {
  if (!courseId.value || !documentId.value) return
  emit('confirm', { courseId: courseId.value, documentId: documentId.value })
}

function continueSingle() {
  if (documentList.value.length === 1) {
    emit('confirm', { courseId: courseId.value, documentId: String(documentList.value[0].doc_id) })
  }
}

onMounted(() => {
  store.fetchCourses().catch(() => {})
  if (courseId.value) onCourseChange(courseId.value)
})
</script>

<style scoped>
.cds-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
.doc-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.doc-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-meta {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}
.cds-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  margin-top: 4px;
}
.cds-single {
  padding: 0;
}
</style>
