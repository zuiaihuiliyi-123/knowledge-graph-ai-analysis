<template>
  <div>
    <PageHeader title="学习空间" desc="浏览课程知识图谱，智能问答与个性化学习路径推荐" />

    <!-- 统一学习上下文条（Phase 7）：当前课程 + 当前文档，所有学习 Tab 共享 -->
    <el-card class="page-card ctx-bar">
      <div class="ctx-left">
        <template v-if="currentCourseId && currentDocumentId">
          <span class="ctx-label">当前课程</span>
          <b class="ctx-name">{{ currentCourseName }}</b>
          <el-divider direction="vertical" />
          <span class="ctx-label">当前文档</span>
          <b class="ctx-name">{{ currentDocumentName }}</b>
        </template>
        <span v-else class="ctx-empty"><el-icon><Compass /></el-icon>请选择课程和学习资料</span>
      </div>
      <div class="ctx-actions">
        <el-button
          v-if="currentCourseId && currentDocumentId"
          type="primary"
          :icon="Reading"
          @click="readCurrentDocument"
        >在线阅读</el-button>
        <el-button type="primary" plain @click="openSelector">
          {{ currentCourseId && currentDocumentId ? '切换资料' : '选择资料' }}
        </el-button>
      </div>
    </el-card>

    <el-tabs v-model="activeTab" class="main-view-tabs" @tab-change="onTabChange">
      <!-- ===================== Tab 0：学习总览（学习驾驶舱，P11：复用统一学习状态，不新建状态源） ===================== -->
      <el-tab-pane name="overview">
        <template #label><span class="tab-label"><el-icon><DataAnalysis /></el-icon>学习总览</span></template>

        <!-- 页头：当前课程 + 欢迎语 -->
        <el-card class="page-card">
          <div class="ov-hero">
            <div class="ov-hero-left">
              <div class="ov-title">学习总览</div>
              <div class="ov-sub">欢迎回来，{{ store.username }}，继续你的学习</div>
            </div>
          </div>
        </el-card>

        <!-- 未选择课程：内联课程/资料选择面板，减少空白 -->
        <el-card v-if="!ctxKey" class="page-card welcome-card">
          <div class="welcome-head">
            <div class="welcome-title">欢迎回来，{{ store.realName || store.username }}</div>
            <div class="welcome-sub">选择课程和学习资料，立即生成你的学习驾驶舱：掌握情况、继续学习、建议重点一目了然</div>
          </div>
          <CourseDocumentSelector
            :initial-course-id="currentCourseId"
            :initial-document-id="currentDocumentId"
            @confirm="applyContext"
          />
        </el-card>
        <!-- /未选择课程 -->

        <template v-else>
          <!-- 第一层：学习概况 KPI（知识点总数 / 已掌握 / 学习进度 / 我的收藏） -->
          <div v-loading="pathDataLoading" class="ov-kpi-row">
            <div v-for="k in overviewKpis" :key="k.label" class="ov-kpi">
              <div class="ov-kpi-icon" :style="{ background: k.color + '1a', color: k.color }"><el-icon :size="22"><component :is="k.icon" /></el-icon></div>
              <div class="ov-kpi-meta">
                <div class="ov-kpi-value">{{ k.value }}</div>
                <div class="ov-kpi-label">{{ k.label }}</div>
              </div>
            </div>
          </div>

          <!-- 第二层：掌握情况 + 继续学习 -->
          <el-row :gutter="16" class="ov-row">
            <el-col :xs="24" :md="12">
              <el-card class="page-card ov-card">
                <template #header>
                  <div class="ov-card-title"><el-icon><Histogram /></el-icon>知识点掌握情况</div>
                </template>
                <div v-loading="pathDataLoading" class="ov-state-list">
                  <div v-for="s in overviewStateList" :key="s.key" class="ov-state-row">
                    <span class="ov-state-glyph" :class="'st-' + s.key">{{ s.glyph }}</span>
                    <span class="ov-state-name">{{ s.label }}</span>
                    <el-progress
                      class="ov-state-bar"
                      :percentage="s.pct"
                      :stroke-width="8"
                      :color="s.color"
                      :show-text="false"
                    />
                    <span class="ov-state-num">{{ s.count }}</span>
                  </div>
                  <div class="ov-state-total">
                    共 {{ overviewTotal }} 个知识点 · 学习进度 {{ overviewPct }}%
                  </div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-card class="page-card ov-card">
                <template #header>
                  <div class="ov-card-title"><el-icon><Guide /></el-icon>继续学习</div>
                </template>
                <div v-if="overviewCurrentNode" class="ov-continue">
                  <div class="ov-current">
                    <span class="ov-state-glyph st-current">●</span>
                    <div class="ov-current-meta">
                      <div class="ov-current-name">{{ overviewCurrentNode.label }}</div>
                      <el-tag size="small" effect="plain" :type="categoryTagType(overviewCurrentNode.properties?.category)">
                        {{ overviewCurrentNode.properties?.category || '知识点' }}
                      </el-tag>
                    </div>
                  </div>
                  <div v-if="overviewRecommended.length" class="ov-next">
                    <div class="ov-next-title">推荐下一步</div>
                    <div
                      v-for="r in overviewRecommended"
                      :key="r.name"
                      class="ov-next-item"
                      @click="overviewViewName(r.name)"
                    >
                      <span class="ov-state-glyph st-recommended">★</span>
                      <span class="ov-next-name">{{ r.name }}</span>
                      <el-icon class="ov-next-jump"><Right /></el-icon>
                    </div>
                  </div>
                  <div class="ov-actions">
                    <el-button type="primary" @click="overviewContinue">继续学习</el-button>
                    <el-button @click="overviewGoPath">查看学习路径</el-button>
                  </div>
                </div>
                <el-empty
                  v-else
                  description="暂无当前学习内容，标记已掌握知识点后将生成推荐"
                  :image-size="60"
                />
              </el-card>
            </el-col>
          </el-row>

          <!-- 第三层：建议重点学习 + 我的收藏 -->
          <el-row :gutter="16" class="ov-row">
            <el-col :xs="24" :md="12">
              <el-card class="page-card ov-card">
                <template #header>
                  <div class="ov-card-title"><el-icon><Aim /></el-icon>建议重点学习</div>
                </template>
                <div v-if="overviewFocusList.length" class="ov-focus-list">
                  <div
                    v-for="f in overviewFocusList"
                    :key="f.kpId || f.name"
                    class="ov-focus-item"
                    @click="overviewViewFocus(f)"
                  >
                    <div class="ov-focus-head">
                      <span class="ov-focus-name">{{ f.name }}</span>
                      <el-tag size="small" effect="plain" :type="categoryTagType(f.category)">{{ f.category || '知识点' }}</el-tag>
                      <el-button size="small" text type="primary" class="ov-focus-view" @click.stop="overviewViewFocus(f)">查看</el-button>
                    </div>
                    <div v-if="f.reason" class="ov-focus-reason">{{ f.reason }}</div>
                  </div>
                </div>
                <el-empty v-else description="暂无推荐，请先在课程中标记已掌握知识点" :image-size="60" />
              </el-card>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-card class="page-card ov-card">
                <template #header>
                  <div class="ov-card-title">
                    <el-icon><StarFilled /></el-icon>我的收藏
                    <span class="ov-card-count">{{ overviewFavIds.length }}</span>
                  </div>
                </template>
                <div v-if="overviewRecentFavs.length" class="ov-fav-list">
                  <div v-for="n in overviewRecentFavs" :key="n.id" class="ov-fav-item" @click="overviewViewFav(n)">
                    <el-icon class="ov-fav-star"><StarFilled /></el-icon>
                    <span class="ov-fav-name">{{ n.label }}</span>
                    <el-tag size="small" effect="plain" :type="categoryTagType(n.properties?.category)">{{ n.properties?.category || '知识点' }}</el-tag>
                    <el-icon class="ov-next-jump"><Right /></el-icon>
                  </div>
                </div>
                <el-empty v-else description="还没有收藏知识点" :image-size="60" />
                <div class="ov-actions">
                  <el-button @click="overviewGoFavorites">查看收藏夹</el-button>
                </div>
              </el-card>
            </el-col>
          </el-row>

        </template>
      </el-tab-pane>

      <!-- ===================== Tab：课程文档（在线阅读入口） ===================== -->
      <el-tab-pane name="documents">
        <template #label><span class="tab-label"><el-icon><Document /></el-icon>课程文档</span></template>

        <el-card class="page-card">
          <div class="sd-head">
            <div class="sd-head-left">
              <span class="sd-title">课程文档</span>
              <span v-if="currentCourseName" class="sd-course">{{ currentCourseName }}</span>
            </div>
            <div class="sd-head-right">
              <el-input
                v-model="docFilter"
                placeholder="搜索文档名"
                clearable
                size="small"
                style="width: 180px"
              />
              <el-button :icon="Refresh" circle size="small" title="刷新" @click="loadCourseDocuments" />
            </div>
          </div>
        </el-card>

        <el-empty
          v-if="!currentCourseId"
          description="请先选择课程，查看该课程的文档"
          :image-size="90"
        >
          <el-button type="primary" @click="openSelector">选择课程</el-button>
        </el-empty>

        <el-card v-else v-loading="docLoading" class="page-card">
          <el-empty
            v-if="!docLoading && !filteredDocuments.length"
            :description="docFilter ? '没有匹配的文档' : '该课程暂无文档'"
            :image-size="80"
          />
          <ul v-else class="sd-list">
            <li v-for="d in filteredDocuments" :key="d.doc_id" class="sd-item">
              <div class="sd-item-main">
                <div class="sd-item-top">
                  <span class="sd-item-name" :title="d.file_name">{{ d.file_name }}</span>
                  <span class="sd-badge">{{ d.file_type }}</span>
                  <span v-if="docProgress[d.doc_id]?.percent > 1" class="sd-badge is-progress">
                    已读 {{ docProgress[d.doc_id].percent }}%
                  </span>
                  <span v-if="docProgress[d.doc_id]?.percent >= 99" class="sd-badge is-done">已读完</span>
                </div>
                <div class="sd-item-meta">
                  {{ fmtDocSize(d.file_size) }} · {{ d.entity_count || 0 }} 个知识点 ·
                  {{ d.relation_count || 0 }} 条关系
                  <template v-if="docProgress[d.doc_id]?.page > 1">
                    · 上次读到第 {{ docProgress[d.doc_id].page }} 页
                  </template>
                </div>
                <div v-if="docProgress[d.doc_id]?.percent > 1" class="sd-item-bar">
                  <div class="sd-item-bar-fill" :style="{ width: docProgress[d.doc_id].percent + '%' }" />
                </div>
              </div>
              <div class="sd-item-actions">
                <el-button type="primary" :icon="Reading" @click="readDocument(d)">
                  {{ docProgress[d.doc_id]?.percent > 1 ? '继续阅读' : '在线阅读' }}
                </el-button>
                <el-button :icon="Download" plain @click="downloadDocument(d)">下载</el-button>
              </div>
            </li>
          </ul>
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 1：图谱浏览 ===================== -->
      <el-tab-pane name="browse">
        <template #label><span class="tab-label"><el-icon><Compass /></el-icon>图谱浏览</span></template>
        <el-card class="page-card">
          <div class="toolbar">
            <el-input
              v-model="searchText"
              placeholder="搜索知识点名称或描述…"
              clearable
              style="width: 260px"
            />
            <el-button :icon="Refresh" circle title="刷新" @click="refreshBrowse" />
            <span v-if="stats" class="stats-text">
              节点 {{ stats.visibleNodeCount }} / {{ stats.nodeCount }} · 关系 {{ stats.visibleEdgeCount }} / {{ stats.edgeCount }}
            </span>
          </div>
        </el-card>
        <el-card class="page-card graph-card">
          <GraphCanvas
            ref="browseGraphRef"
            :course-id="currentCourseId"
            :document-id="currentDocumentId"
            :search-text="searchText"
            :mastered-kp-ids="masteredKpIds"
            :highlight-path="highlightPathNodes"
            :current-kp-id="browseCurrentKpId"
            :recommended-kp-ids="browseRecommendedKpIds"
            focus-on-click
            progressive
            @node-click="onNodeClick"
            @stats="(s) => (stats = s)"
          />
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 2：智能问答（证据链） ===================== -->
      <el-tab-pane name="qa">
        <template #label><span class="tab-label"><el-icon><ChatDotRound /></el-icon>智能问答</span></template>

        <el-row :gutter="16" class="qa-layout">
          <!-- 左栏：当前课程 + 相关知识点 -->
          <el-col :xs="24" :sm="6">
            <el-card class="page-card qa-side">
              <template #header>
                <div class="qa-side-title"><el-icon><Collection /></el-icon>当前课程</div>
              </template>
              <div class="qa-side-course">{{ currentCourseName }}</div>

              <el-divider content-position="left">相关知识点</el-divider>
              <el-empty
                v-if="!relatedKps.length"
                description="提问后，此处展示答案引用的知识点"
                :image-size="60"
              />
              <div v-else class="kp-list">
                <div v-for="kp in relatedKps" :key="kp.kp_id || kp.name" class="kp-item" @click="jumpToKp(kp)">
                  <el-tag size="small" effect="plain" :type="categoryTagType(kp.category)">
                    {{ kp.category || '知识点' }}
                  </el-tag>
                  <span class="kp-name">{{ kp.name }}</span>
                  <el-icon class="kp-jump"><Right /></el-icon>
                </div>
              </div>
            </el-card>
          </el-col>

          <!-- 右栏：AI 学习助手 -->
          <el-col :xs="24" :sm="18">
            <el-card class="qa-card qa-main">
              <div class="qa-toolbar">
                <span class="qa-title"><el-icon><MagicStick /></el-icon>AI 学习助手</span>
                <el-button size="small" @click="clearChat">清空对话</el-button>
              </div>

              <div ref="chatBoxRef" class="chat-box chat-scroll">
                <div v-if="!messages.length" class="chat-welcome">
                  <div class="welcome-icon"><el-icon :size="40" color="var(--color-primary)"><MagicStick /></el-icon></div>
                  <p>基于课程知识图谱的 AI 问答助手</p>
                  <p class="welcome-sub">例如：什么是线性表？栈和队列有什么区别？</p>
                </div>

                <div
                  v-for="(m, i) in messages"
                  :key="i"
                  class="msg-row"
                  :class="m.role === 'user' ? 'msg-user' : 'msg-ai'"
                >
                  <div class="msg-bubble">
                    <!-- 错误态：LLM 生成失败（网络/接口异常也归为此类） -->
                    <div v-if="m.error" class="msg-error">
                      <el-icon><WarningFilled /></el-icon>
                      <span>AI 服务暂时不可用，请稍后重试</span>
                    </div>
                    <template v-else>
                      <div v-if="m.role === 'ai'" class="msg-text msg-md" v-html="renderMarkdown(m.content)"></div>
                      <div v-else class="msg-text">{{ m.content }}</div>
                      <!-- 证据链：回答正文 → 引用来源 → 相关知识点（点击可定位图谱，始终按 kp_id 对齐） -->
                      <div v-if="m.role === 'ai'" class="msg-sources">
                        <div class="sources-title">
                          <el-icon><Document /></el-icon> 参考来源 · 课程知识库
                        </div>
                        <template v-if="m.sources && m.sources.length">
                          <div
                            v-for="(s, j) in m.sources"
                            :key="s.kp_id || s.name || j"
                            class="source-card"
                            @click="jumpToKp(s)"
                          >
                            <div class="source-head">
                              <el-tag size="small" effect="plain" :type="categoryTagType(s.category)">
                                {{ s.category || '知识点' }}
                              </el-tag>
                              <span class="source-name">{{ s.name }}</span>
                              <el-button
                                v-if="s.kp_id && ctxKey"
                                size="small"
                                text
                                class="source-fav"
                                :title="isFavorited(s.kp_id) ? '取消收藏' : '收藏'"
                                :type="isFavorited(s.kp_id) ? 'warning' : 'info'"
                                @click.stop="toggleFavoriteSafe(s.kp_id)"
                              >
                                <el-icon><StarFilled v-if="isFavorited(s.kp_id)" /><Star v-else /></el-icon>
                              </el-button>
                              <el-icon class="source-jump"><Right /></el-icon>
                            </div>
                            <div v-if="s.description" class="source-desc">{{ s.description }}</div>
                          </div>
                        </template>
                        <div v-else class="sources-empty">未检索到相关知识点（AI 基于通用知识回答，仅供参考）</div>
                      </div>
                    </template>
                  </div>
                </div>

                <div v-if="asking" class="msg-row msg-ai">
                  <div class="msg-bubble typing">
                    <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
                  </div>
                </div>
              </div>

              <div class="chat-input">
                <el-input
                  v-model="question"
                  placeholder="输入你的问题，回车发送"
                  :disabled="asking"
                  @keyup.enter="sendQuestion"
                />
                <el-button type="primary" :loading="asking" @click="sendQuestion">
                  发送
                </el-button>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ===================== Tab 3：学习路径推荐 ===================== -->
      <el-tab-pane name="path">
        <template #label><span class="tab-label"><el-icon><Guide /></el-icon>学习路径推荐</span></template>

        <!-- P5：学习路径视觉层级（已掌握 → 当前 → 推荐 → 未学习） -->
        <el-card class="page-card path-hero">
          <template #header>
            <div class="path-hero-header">
              <span class="path-hero-title"><el-icon><Guide /></el-icon>学习路径</span>
            </div>
          </template>

          <el-empty
            v-if="!ctxKey"
            description="请先选择课程和学习资料，查看你的学习路径"
            :image-size="80"
          />

          <template v-else>
            <!-- 学习进度摘要 -->
            <div class="path-summary">
              <div class="summary-left">
                <div class="summary-line">
                  已掌握 <b class="num">{{ pathProgress.mastered }}</b>
                  <span class="muted">/ {{ pathProgress.total }} 个知识点</span>
                </div>
                <div v-if="pathProgress.current" class="summary-suggestion">
                  当前建议：继续学习「<b>{{ pathProgress.current }}</b>」
                </div>
                <div
                  v-else-if="pathProgress.total && pathProgress.mastered >= pathProgress.total"
                  class="summary-suggestion success"
                >
                  <el-icon><CircleCheckFilled /></el-icon> 太棒了，本课程知识点已全部掌握
                </div>
                <div v-else class="summary-suggestion muted">
                  完成知识点标记后，这里会给出下一步学习建议
                </div>
              </div>
              <div class="summary-progress">
                <el-progress
                  :percentage="pathProgress.pct"
                  :stroke-width="14"
                  :text-inside="true"
                  color="linear-gradient(90deg,#5b8def,#8b5cf6)"
                />
              </div>
            </div>

            <!-- 路径链 -->
            <div v-loading="pathDataLoading" class="path-chain-wrap">
              <el-empty
                v-if="!pathDataLoading && pathDataChecked && !pathChain.length"
                :description="pathProgress.total && pathProgress.mastered >= pathProgress.total ? '已掌握全部知识点' : '请先在「图谱浏览」中标记已掌握知识点，系统据此推荐学习路径'"
                :image-size="60"
              />
              <div v-else-if="pathChain.length" class="path-chain">
                <template v-for="(seg, i) in pathChain" :key="seg.node.id">
                  <div
                    class="chain-node"
                    :class="'state-' + seg.state"
                    title="点击在图谱中定位并查看详情"
                    @click="locateKnowledgePoint(seg.node)"
                  >
                    <div class="chain-icon">{{ STATE_META[seg.state].glyph }}</div>
                    <div class="chain-content">
                      <div class="chain-head">
                        <span class="chain-name">{{ seg.node.label }}</span>
                        <el-tag size="small" effect="plain" :type="categoryTagType(seg.node.properties?.category)">
                          {{ seg.node.properties?.category || '知识点' }}
                        </el-tag>
                        <span class="state-label" :class="'state-label-' + seg.state">{{ STATE_META[seg.state].label }}</span>
                        <el-button
                          v-if="ctxKey && seg.node.id != null"
                          size="small"
                          text
                          class="chain-fav"
                          :title="isFavorited(seg.node.id) ? '取消收藏' : '收藏'"
                          :type="isFavorited(seg.node.id) ? 'warning' : 'info'"
                          @click.stop="toggleFavoriteSafe(seg.node.id)"
                        >
                          <el-icon><StarFilled v-if="isFavorited(seg.node.id)" /><Star v-else /></el-icon>
                        </el-button>
                      </div>
                      <div v-if="seg.reason" class="chain-reason">原因：{{ seg.reason }}</div>
                      <div v-if="seg.state === 'recommended'" class="chain-status">当前状态：未掌握</div>
                    </div>
                  </div>
                  <div v-if="i < pathChain.length - 1" class="chain-link">
                    <el-icon><ArrowDown /></el-icon>
                    <span>前置知识</span>
                  </div>
                </template>
              </div>
            </div>

            <div class="path-actions">
              <el-button type="primary" size="large" :disabled="!pathProgress.current" @click="startLearning">
                开始学习
              </el-button>
            </div>
          </template>
        </el-card>

        <el-row :gutter="16">
          <!-- 推荐下一步 -->
          <el-col :xs="24" :sm="12">
            <el-card class="page-card">
              <template #header>
                <b><el-icon class="icon-gap" color="var(--color-primary)"><Aim /></el-icon>推荐下一步学习内容</b>
              </template>
              <p class="tip">输入你已掌握的知识点（每行一个），系统根据图谱前置关系推荐可学习的知识点</p>
              <el-input
                v-model="masteredText"
                type="textarea"
                :rows="5"
                placeholder="Python基础&#10;数据结构绪论&#10;…"
              />
              <div class="btn-row">
                <el-button type="primary" :loading="recommendLoading" @click="doRecommend">
                  推荐下一步
                </el-button>
              </div>

              <template v-if="recommendations.length">
                <el-divider content-position="left">推荐结果（{{ recommendations.length }}）</el-divider>
                <div v-for="r in recommendations" :key="r.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" :type="categoryTagType(r.category)">{{ r.category }}</el-tag>
                    <b>{{ r.name }}</b>
                  </div>
                  <div class="rec-desc">{{ r.description }}</div>
                  <div class="rec-reason"><el-icon class="icon-gap" color="var(--color-warning)"><Opportunity /></el-icon>{{ r.reason }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="recommendChecked && !recommendLoading"
                description="未找到可推荐的知识点（请确认已掌握知识点名称与图谱一致）"
                :image-size="60"
              />
            </el-card>
          </el-col>

          <!-- 目标路径 + 前置知识 -->
          <el-col :xs="24" :sm="12">
            <el-card class="page-card">
              <template #header>
                <b><el-icon class="icon-gap" color="var(--color-primary)"><Guide /></el-icon>到达目标知识点的学习路径</b>
              </template>
              <p class="tip">输入目标知识点，系统自动生成从入门到该知识点的最短学习路径</p>
              <div class="btn-row">
                <el-input v-model="targetKnowledge" placeholder="目标知识点，如：二叉树的遍历" style="max-width: 320px" />
                <el-button type="primary" :loading="pathLoading" @click="doPathToTarget">
                  生成路径
                </el-button>
              </div>

              <template v-if="paths.length">
                <el-divider content-position="left">共 {{ paths.length }} 条路径</el-divider>
                <div v-for="(path, pi) in paths" :key="pi" class="path-chain">
                  <div class="path-index">路径 {{ pi + 1 }}</div>
                  <div class="path-steps">
                    <template v-for="(step, si) in path" :key="si">
                      <span class="path-step">
                        <el-tag size="small">{{ step.category || '知识点' }}</el-tag>
                        <span class="step-name">{{ step.name }}</span>
                      </span>
                      <span v-if="si < path.length - 1" class="path-arrow">→</span>
                    </template>
                  </div>
                </div>
              </template>
              <template v-else-if="pathFallback">
                <div class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" :type="categoryTagType(pathTargetNode?.category)">
                      {{ pathTargetNode?.category || '知识点' }}
                    </el-tag>
                    <b>{{ pathTargetNode?.name || targetKnowledge }}</b>
                  </div>
                  <div class="rec-desc">{{ pathTargetNode?.description }}</div>
                  <div class="rec-reason"><el-icon class="icon-gap" color="var(--color-warning)"><Opportunity /></el-icon>{{ pathReason }}</div>
                </div>
                <el-divider content-position="left">相关概念（{{ pathRelated.length }}）</el-divider>
                <div v-for="r in pathRelated" :key="r.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" :type="categoryTagType(r.category)">{{ r.category }}</el-tag>
                    <b>{{ r.name }}</b>
                  </div>
                  <div class="rec-desc">{{ r.description }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="pathChecked && !pathLoading"
                description="未找到该知识点（请确认名称与图谱一致）"
                :image-size="60"
              />
            </el-card>

            <el-card class="page-card">
              <template #header>
                <b><el-icon class="icon-gap" color="var(--color-primary)"><Search /></el-icon>前置知识查询</b>
              </template>
              <div class="btn-row">
                <el-input v-model="prereqName" placeholder="知识点名称" style="max-width: 320px" />
                <el-button :loading="prereqLoading" @click="doQueryPrereqs">查询</el-button>
              </div>
              <template v-if="prereqResult && prereqResult.count">
                <el-divider content-position="left">共 {{ prereqResult.count }} 个前置知识</el-divider>
                <div v-for="p in prereqResult.prerequisites" :key="p.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" type="info">{{ p.reason || `${p.depth} 级前置` }}</el-tag>
                    <b>{{ p.name }}</b>
                  </div>
                  <div class="rec-desc">{{ p.description }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="prereqResult && !prereqLoading"
                description="未查询到前置知识（请确认知识点名称准确）"
                :image-size="60"
              />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ===================== Tab 4：收藏夹 ===================== -->
      <el-tab-pane name="favorites">
        <template #label><span class="tab-label"><el-icon><StarFilled /></el-icon>收藏夹</span></template>

        <el-card class="page-card">
          <div class="toolbar">
            <el-input
              v-model="favSearch"
              placeholder="搜索收藏的知识点…"
              clearable
              style="width: 220px"
            />
            <el-select v-model="favCategory" placeholder="全部类型" clearable style="width: 140px">
              <el-option label="概念" value="概念" />
              <el-option label="定理" value="定理" />
              <el-option label="公式" value="公式" />
              <el-option label="方法" value="方法" />
            </el-select>
          </div>

          <div v-if="ctxKey" class="fav-count">
            共收藏 <b class="num">{{ favTotal }}</b> 个知识点
          </div>

          <div v-loading="favLoading" class="fav-list">
            <el-empty
              v-if="!favLoading && !ctxKey"
              description="请先选择课程和学习资料，查看该文档的收藏"
              :image-size="80"
            />
            <el-empty
              v-else-if="!favLoading && favLoaded && favTotal === 0"
              :image-size="80"
            >
              <template #description>
                <p class="fav-empty-title">还没有收藏知识点</p>
                <p class="fav-empty-sub">在学习过程中收藏感兴趣或需要复习的知识点，它们会集中出现在这里。</p>
              </template>
              <el-button type="primary" @click="goBrowse">去浏览知识图谱</el-button>
            </el-empty>
            <el-empty
              v-else-if="!favLoading && favLoaded && favoriteList.length === 0"
              description="没有符合条件的收藏"
              :image-size="80"
            />
            <div v-else-if="favoriteList.length" class="fav-grid">
              <div v-for="node in favoriteList" :key="node.id" class="fav-card">
                <div class="fav-card-head">
                  <el-icon class="fav-star"><StarFilled /></el-icon>
                  <span class="fav-name">{{ node.label }}</span>
                  <el-tag size="small" effect="plain" :type="categoryTagType(node.properties?.category)">
                    {{ node.properties?.category || '知识点' }}
                  </el-tag>
                </div>
                <div class="fav-desc">{{ node.description || '暂无描述' }}</div>
                <div class="fav-actions">
                  <el-button size="small" type="primary" plain @click="viewFavorite(node)">
                    查看知识点
                  </el-button>
                  <el-button size="small" type="danger" plain @click="toggleFavoriteSafe(node.id)">
                    取消收藏
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 5：做题练习（Scope A：单选/多选/判断，提交后判定与解析） ===================== -->
      <el-tab-pane name="practice">
        <template #label><span class="tab-label"><el-icon><Collection /></el-icon>做题练习</span></template>

        <el-card v-if="!ctxKey" class="page-card">
          <el-empty description="请先选择课程和学习资料，开始做题练习" :image-size="90">
            <el-button type="primary" @click="openSelector">选择课程/文档</el-button>
          </el-empty>
        </el-card>

        <template v-else>
          <!-- 练习设置 + 我的练习统计 -->
          <el-card class="page-card">
            <div class="chart-title-line"><el-icon><Opportunity /></el-icon> 练习设置</div>
            <div class="table-toolbar">
              <el-radio-group v-model="practiceScope">
                <el-radio value="document">本学习资料</el-radio>
                <el-radio value="course">整门课程</el-radio>
              </el-radio-group>
              <el-select v-model="practiceQType" placeholder="全部题型（默认仅客观题）" clearable style="width: 170px">
                <el-option label="单选题" value="SINGLE" />
                <el-option label="多选题" value="MULTI" />
                <el-option label="判断题" value="JUDGE" />
                <el-option label="填空题（需教师批改）" value="FILL" />
                <el-option label="解答题（需教师批改）" value="ESSAY" />
              </el-select>
              <el-select v-model="practiceKpId" placeholder="全部知识点" clearable filterable style="width: 200px">
                <el-option v-for="n in practiceKpOptions" :key="n.id" :label="n.label" :value="n.id" />
              </el-select>
              <el-select v-model="practiceCount" style="width: 110px">
                <el-option :value="5" label="5 题" />
                <el-option :value="10" label="10 题" />
                <el-option :value="20" label="20 题" />
              </el-select>
              <el-button type="primary" :icon="Opportunity" :loading="practiceLoading" @click="startPractice">开始练习</el-button>
              <el-button :icon="MagicStick" :loading="practiceLoading" @click="startSmartPractice">智能推荐出题</el-button>
              <el-select v-model="practiceMode" style="width: 130px" :disabled="practiceLoading">
                <el-option v-for="m in PRACTICE_MODES" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
              <el-button :icon="MagicStick" @click="practiceByRecommendation">按推荐知识点出题</el-button>
            </div>
            <div v-loading="practiceStatsLoading" class="practice-kpis">
              <div class="practice-kpi">
                <div class="practice-kpi-value">{{ practiceStats?.answer_count ?? 0 }}</div>
                <div class="practice-kpi-label">累计作答</div>
              </div>
              <div class="practice-kpi">
                <div class="practice-kpi-value">{{ practiceStats?.correct_rate ?? 0 }}%</div>
                <div class="practice-kpi-label">正确率</div>
              </div>
              <div class="practice-kpi">
                <div class="practice-kpi-value">{{ practiceStats?.wrong_question_count ?? 0 }}</div>
                <div class="practice-kpi-label">错题数</div>
              </div>
              <div class="practice-kpi">
                <div class="practice-kpi-value">{{ practiceStats?.favorite_count ?? 0 }}</div>
                <div class="practice-kpi-label">收藏题目</div>
              </div>
            </div>
          </el-card>

          <el-tabs v-model="practiceSubTab">
            <!-- 子页 1：答题 -->
            <el-tab-pane name="doing">
              <template #label><span class="tab-label"><el-icon><EditPen /></el-icon>练习答题</span></template>

              <el-card class="page-card">
                <div v-if="practiceRecommendHint" class="practice-reco-hint">
                  <el-icon><DataAnalysis /></el-icon> {{ practiceRecommendHint }}
                </div>
                <el-empty
                  v-if="!practiceList.length && !practiceLoading"
                  description="还没有题目。请在上方设置出题条件后点击「开始练习」"
                  :image-size="80"
                />

                <div v-else-if="currentPracticeQuestion" v-loading="practiceLoading" class="practice-card">
                  <div class="practice-head">
                    <el-tag size="small" effect="plain">{{ currentPracticeQuestion.q_type_label }}</el-tag>
                    <el-tag size="small" type="info" effect="plain">难度 {{ '★'.repeat(currentPracticeQuestion.difficulty || 0) }}</el-tag>
                    <el-tag
                      v-if="currentPracticeQuestion.bucket_label"
                      size="small"
                      type="warning"
                      effect="plain"
                    >智能推荐 · {{ currentPracticeQuestion.bucket_label }}</el-tag>
                    <el-tag
                      v-if="currentPracticeQuestion.kp_name"
                      size="small"
                      effect="plain"
                    >{{ currentPracticeQuestion.kp_name }}</el-tag>
                    <span class="practice-progress">第 {{ practiceIndex + 1 }} / {{ practiceList.length }} 题</span>
                    <div class="practice-head-actions">
                      <el-button
                        size="small"
                        :icon="isPracticeFavorited(currentPracticeQuestion) ? StarFilled : Star"
                        @click="togglePracticeFavorite(currentPracticeQuestion)"
                      >{{ isPracticeFavorited(currentPracticeQuestion) ? '已收藏' : '收藏' }}</el-button>
                      <el-button size="small" @click="viewPracticeKp(currentPracticeQuestion)">查看知识点</el-button>
                    </div>
                  </div>

                  <div v-if="currentPracticeQuestion.reason" class="practice-reason">
                    <el-icon><MagicStick /></el-icon> {{ currentPracticeQuestion.reason }}
                  </div>

                  <div class="practice-stem">{{ currentPracticeQuestion.stem }}</div>

                  <!-- 单选 -->
                  <el-radio-group
                    v-if="currentPracticeQuestion.q_type === 'SINGLE'"
                    v-model="practiceAnswers[currentPracticeQuestion.question_id]"
                    class="practice-options"
                    :disabled="!!practiceResults[currentPracticeQuestion.question_id]"
                  >
                    <el-radio v-for="o in currentPracticeQuestion.options" :key="o.key" :value="o.key" class="practice-option">
                      {{ o.key }}. {{ o.text }}
                    </el-radio>
                  </el-radio-group>

                  <!-- 多选 -->
                  <el-checkbox-group
                    v-else-if="currentPracticeQuestion.q_type === 'MULTI'"
                    v-model="practiceAnswers[currentPracticeQuestion.question_id]"
                    class="practice-options"
                    :disabled="!!practiceResults[currentPracticeQuestion.question_id]"
                  >
                    <el-checkbox v-for="o in currentPracticeQuestion.options" :key="o.key" :value="o.key" class="practice-option">
                      {{ o.key }}. {{ o.text }}
                    </el-checkbox>
                  </el-checkbox-group>

                  <!-- 判断 -->
                  <el-radio-group
                    v-else-if="currentPracticeQuestion.q_type === 'JUDGE'"
                    v-model="practiceAnswers[currentPracticeQuestion.question_id]"
                    class="practice-options"
                    :disabled="!!practiceResults[currentPracticeQuestion.question_id]"
                  >
                    <el-radio value="true" class="practice-option">正确</el-radio>
                    <el-radio value="false" class="practice-option">错误</el-radio>
                  </el-radio-group>

                  <!-- 填空（Scope B：多空输入；提交后进入教师批改队列，批改前不下发参考答案） -->
                  <div v-else-if="currentPracticeQuestion.q_type === 'FILL'" class="practice-blanks">
                    <div v-for="b in currentPracticeQuestion.options" :key="b.key" class="practice-blank-row">
                      <span class="practice-blank-label">{{ b.label }}</span>
                      <el-input
                        v-model="practiceAnswers[currentPracticeQuestion.question_id][b.key]"
                        :placeholder="b.hint || '填写答案'"
                        :disabled="!!practiceResults[currentPracticeQuestion.question_id]"
                        style="width: 260px"
                      />
                    </div>
                  </div>

                  <!-- 解答（Scope B：主观题，教师批改后给分） -->
                  <el-input
                    v-else
                    v-model="practiceAnswers[currentPracticeQuestion.question_id]"
                    type="textarea"
                    :rows="5"
                    maxlength="4000"
                    show-word-limit
                    placeholder="请写出你的解答过程（提交后由教师批改）"
                    :disabled="!!practiceResults[currentPracticeQuestion.question_id]"
                  />

                  <!-- 判定结果（客观题提交后即下发答案；主观题批改后才下发） -->
                  <div
                    v-if="practiceResults[currentPracticeQuestion.question_id]"
                    class="practice-result"
                    :class="resultClass(currentPracticeQuestion.question_id)"
                  >
                    <div class="practice-result-head">
                      <el-icon>
                        <component :is="resultIcon(currentPracticeQuestion.question_id)" />
                      </el-icon>
                      {{ resultText(currentPracticeQuestion.question_id) }}
                      <span
                        v-if="!practiceResults[currentPracticeQuestion.question_id].pending"
                        class="practice-result-answer"
                      >
                        正确答案：{{ formatAnswer(practiceResults[currentPracticeQuestion.question_id].correct_answer) }}
                      </span>
                    </div>
                    <div
                      v-if="practiceResults[currentPracticeQuestion.question_id].comment"
                      class="practice-analysis"
                    >
                      教师评语：{{ practiceResults[currentPracticeQuestion.question_id].comment }}
                    </div>
                    <div v-if="practiceResults[currentPracticeQuestion.question_id].analysis" class="practice-analysis">
                      解析：{{ practiceResults[currentPracticeQuestion.question_id].analysis }}
                    </div>
                  </div>

                  <div class="practice-actions">
                    <el-button :disabled="practiceIndex === 0" @click="practiceIndex--">上一题</el-button>
                    <el-button
                      v-if="!practiceResults[currentPracticeQuestion.question_id]"
                      type="primary"
                      :loading="practiceSubmitting"
                      @click="submitCurrent"
                    >提交答案</el-button>
                    <el-button
                      v-else
                      type="primary"
                      :disabled="practiceIndex >= practiceList.length - 1"
                      @click="practiceIndex++"
                    >下一题</el-button>
                    <el-button
                      v-if="practiceIndex >= practiceList.length - 1 && practiceResults[currentPracticeQuestion.question_id]"
                      @click="startPractice"
                    >再练一组</el-button>
                  </div>
                </div>
              </el-card>
            </el-tab-pane>

            <!-- 子页 2：错题本 -->
            <el-tab-pane name="wrong">
              <template #label><span class="tab-label"><el-icon><WarningFilled /></el-icon>错题本</span></template>
              <el-card class="page-card">
                <div class="table-toolbar">
                  <el-button :icon="Refresh" @click="loadWrongBook">刷新</el-button>
                  <span class="practice-tip">按题去重，取每题最近一次答错记录；答对后不会自动移除，便于复习巩固。</span>
                </div>
                <el-table :data="wrongList" v-loading="wrongLoading" row-key="question_id">
                  <el-table-column prop="stem" label="题目" min-width="240" show-overflow-tooltip />
                  <el-table-column label="题型" width="90">
                    <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.q_type_label }}</el-tag></template>
                  </el-table-column>
                  <el-table-column label="知识点" width="150">
                    <template #default="{ row }">
                      <span v-if="row.kp_name">{{ row.kp_name }}</span>
                      <span v-else class="cell-empty">—</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="wrong_count" label="错答次数" width="90" />
                  <el-table-column label="最近错误作答" width="140">
                    <template #default="{ row }">{{ formatAnswer(row.last_user_answer) }}</template>
                  </el-table-column>
                  <!-- G0b：主观题批改结果对学生可见（教师给分与评语；客观题此处为 —） -->
                  <el-table-column label="教师给分" width="100">
                    <template #default="{ row }">
                      <span v-if="row.grade_source === 'TEACHER' && row.last_score !== null && row.last_score !== undefined">
                        {{ row.last_score }} 分
                      </span>
                      <span v-else class="cell-empty">—</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="教师评语" min-width="140" show-overflow-tooltip>
                    <template #default="{ row }">
                      <span v-if="row.last_comment">{{ row.last_comment }}</span>
                      <span v-else class="cell-empty">—</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="正确答案" width="120">
                    <template #default="{ row }">{{ formatAnswer(row.correct_answer) }}</template>
                  </el-table-column>
                  <el-table-column prop="last_wrong_at" label="最近错误时间" width="150" />
                  <el-table-column label="操作" width="160" fixed="right">
                    <template #default="{ row }">
                      <el-button size="small" type="primary" link @click="redoQuestion(row)">重做</el-button>
                      <el-button size="small" link @click="viewPracticeKp(row)">知识点</el-button>
                    </template>
                  </el-table-column>
                  <template #empty><el-empty description="还没有错题，继续保持！" :image-size="80" /></template>
                </el-table>
              </el-card>
            </el-tab-pane>

            <!-- 子页 3：我的收藏题目 -->
            <el-tab-pane name="fav">
              <template #label><span class="tab-label"><el-icon><StarFilled /></el-icon>收藏题目</span></template>
              <el-card class="page-card">
                <div class="table-toolbar">
                  <el-button :icon="Refresh" @click="loadMyQuestionFavorites">刷新</el-button>
                  <span class="practice-tip">收藏的题目可随时重做，也可取消收藏。</span>
                </div>
                <el-table :data="myQuestionFavs" v-loading="myQuestionFavLoading" row-key="question_id">
                  <el-table-column prop="stem" label="题目" min-width="280" show-overflow-tooltip />
                  <el-table-column label="题型" width="90">
                    <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.q_type_label }}</el-tag></template>
                  </el-table-column>
                  <el-table-column prop="favorited_at" label="收藏时间" width="160" />
                  <el-table-column label="操作" width="180" fixed="right">
                    <template #default="{ row }">
                      <el-button size="small" type="primary" link @click="redoQuestion(row)">重做</el-button>
                      <el-button size="small" type="danger" link @click="togglePracticeFavorite(row)">取消收藏</el-button>
                    </template>
                  </el-table-column>
                  <template #empty><el-empty description="还没有收藏题目" :image-size="80" /></template>
                </el-table>
              </el-card>
            </el-tab-pane>
          </el-tabs>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- 课程 → 文档选择器（Phase 7） -->
    <el-dialog v-model="selectorVisible" title="选择课程与学习资料" width="560px" :close-on-click-modal="false">
      <CourseDocumentSelector
        :initial-course-id="currentCourseId"
        :initial-document-id="currentDocumentId"
        @confirm="applyContext"
        @cancel="selectorVisible = false"
      />
    </el-dialog>

    <!-- 知识点详情抽屉（只读） -->
    <NodeDetailDrawer
      v-model="drawerVisible"
      :node="drawerNode"
      :course-id="currentCourseId"
      :document-id="currentDocumentId"
      show-mastery
      :mastered="drawerMastered"
      :mastery-loading="masteryLoading"
      show-favorite
      :favorited="drawerFavorited"
      :favorite-loading="favoriteLoading"
      show-expand
      :expanded="drawerExpanded"
      :related="drawerRelated"
      :predecessors="drawerPredecessors"
      :state="drawerLearningState"
      @toggle-mastery="onToggleMastery"
      @toggle-favorite="onToggleFavorite"
      @expand-toggle="onExpandToggle"
      @jump-to="onJumpToNode"
    />
    <!-- AI 助教悬浮窗：任意 Tab 下都可随时唤起问答 -->
    <!-- AI 助手浮窗已收敛为 App.vue 全局唯一实例（避免页面切换时双重实例跳动），课程上下文经 store.learningContext 共享 -->
  </div>
</template>

<script setup>
import { ref, nextTick, watch, computed, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Compass, ChatDotRound, Guide, Aim, Search, Document, Opportunity, MagicStick, ArrowDown, CircleCheckFilled, Right, Collection, WarningFilled, Star, StarFilled, DataAnalysis, Histogram, Odometer, Checked, Reading, Download, EditPen, Clock } from '@element-plus/icons-vue'
import { fetchDocumentBuffer } from '../utils/documentContent'
import { getReadingProgress } from '../utils/readingProgress'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import GraphCanvas from '../components/GraphCanvas.vue'
import CourseDocumentSelector from '../components/CourseDocumentSelector.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'
import { useAppStore } from '../stores/app'
import { renderMarkdown } from '../utils/markdown'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

// ===================== 学习上下文（Phase 7：统一 Course → Document） =====================
// 所有学习 Tab（overview/browse/qa/path/favorites）共享同一份学习上下文，不再各自维护 courseId。
const currentCourseId = computed(() => store.learningContext.currentCourseId)
const currentDocumentId = computed(() => store.learningContext.currentDocumentId)

// 缓存作用域 key：`${courseId}:${documentId}`（禁止仅用 courseId 作为学习状态缓存 key）
const ctxKey = computed(() => {
  const cid = currentCourseId.value
  const did = currentDocumentId.value
  return cid && did ? `${cid}:${did}` : ''
})

const currentCourseName = computed(() => {
  const c = store.courses.find((c) => String(c.course_id) === String(currentCourseId.value))
  return c ? c.course_name : (currentCourseId.value || '')
})
const currentDocumentName = computed(() => {
  const d = store.learningContext.documentList.find((d) => String(d.doc_id) === String(currentDocumentId.value))
  return d ? d.file_name : (currentDocumentId.value || '')
})

// 需要学习上下文的学习 Tab（overview 无需强制上下文）
const LEARNING_TABS = ['browse', 'qa', 'path', 'favorites', 'practice']
// 只需要课程、不需要选定文档的 Tab（课程文档列表本身用于挑文档）
const COURSE_ONLY_TABS = ['documents']
const activeTab = ref(route.query.tab || 'overview')

// 课程 → 文档选择器
const selectorVisible = ref(false)
function openSelector() {
  selectorVisible.value = true
}

// 选择确认：写 store + 同步 URL（ctxKey watcher 负责清空/重载文档级学习状态）
// CourseDocumentSelector 的 confirm 事件载荷是单对象 { courseId, documentId }，这里必须解构
function applyContext({ courseId, documentId }) {
  store.setLearningContext({ courseId, documentId })
  // 填充当前课程的文档列表，供上下文条正确显示文档名（否则切换后文档名会显示成裸 id）
  store.fetchDocuments(courseId).catch(() => {})
  selectorVisible.value = false
  syncUrlToContext()
}

function syncUrlToContext() {
  const query = { tab: activeTab.value }
  if (currentCourseId.value) query.course_id = String(currentCourseId.value)
  if (currentDocumentId.value) query.document_id = String(currentDocumentId.value)
  router.replace({ path: '/student', query })
}

/**
 * 课程中心改造：课程必须是我已加入的（store.courses 后端已收敛为「我的课程」）。
 *
 * 目的：书签 / 外部链接里可能残留别人课程的 course_id，改造后这类访问一律 4003，
 * 若不拦在这里，图谱 / 问答 / 路径等 Tab 会连环弹「无权限」错误。
 * 课程列表尚未加载完成时不误判（返回 true，交给后端最终裁决）。
 */
function isMyCourse(cid) {
  if (!cid) return false
  // 仅在「课程列表还没加载完」时豁免，避免误拦；加载完就以列表为准——
  // 注意不能把「列表为空」也当成豁免条件，那正好是「一门课都没加入」的学生，
  // 恰恰是最需要被拦下的人。
  if (!store.coursesLoaded) return true
  return store.courses.some((c) => String(c.course_id) === String(cid))
}

/** 命中「不属于我的课程」时的统一处理：清空上下文并引导去课程中心加入 */
function rejectForeignCourse() {
  store.clearLearningDocument()
  store.setLearningContext({ courseId: null, documentId: null })
  activeTab.value = 'overview'
  ElMessage.warning('你尚未加入该课程，请先在「课程中心」加入后再学习')
  router.replace({ path: '/course-center', query: { tab: 'join' } })
}

// 进入学习 Tab 前校验上下文：URL → learningContext → 不猜测
function ensureContext() {
  const cid = route.query.course_id
  const did = route.query.document_id
  if (cid && did) {
    store.setLearningContext({ courseId: cid, documentId: did })
    if (!store.learningContext.documentList.length) store.fetchDocuments(cid).catch(() => {})
    return true
  }
  if (currentCourseId.value && currentDocumentId.value) {
    // 修复左侧菜单点击无响应：此处不能再 syncUrlToContext()。
    // 左侧菜单跳转只带 ?tab=新Tab，而此刻 activeTab 仍是旧 Tab，
    // 回写会把 URL 改回旧 Tab 并再次触发路由 watcher，把视图拽回旧页。
    // URL 补参统一由路由 watcher 在 enterTab 成功后处理。
    return true
  }
  return false
}

// 「课程文档」只要求选了课程：没选时同样不猜测，回退到学习总览并提示
function ensureCourseOnly() {
  if (currentCourseId.value) return true
  const cid = route.query.course_id
  if (cid) {
    store.setLearningContext({ courseId: cid, documentId: route.query.document_id || null })
    store.fetchDocuments(cid).catch(() => {})
    return !!currentCourseId.value
  }
  return false
}

// 进入指定 Tab（学习 Tab 缺上下文时不猜测，回退到选择态并提示）
function enterTab(tab) {
  const target = tab || 'overview'

  // 课程中心改造：需要课程的 Tab 先拦「不属于我的课程」（书签/外链里可能残留别人课程的 id），
  // 否则后端会一律 4003，表现为图谱/问答/路径连环弹「无权限」，很难理解。
  const needsCourse = COURSE_ONLY_TABS.includes(target) || LEARNING_TABS.includes(target)
  const cid = route.query.course_id || currentCourseId.value
  if (needsCourse && cid && !isMyCourse(cid)) {
    rejectForeignCourse()
    return
  }

  if (COURSE_ONLY_TABS.includes(target)) {
    if (!ensureCourseOnly()) {
      activeTab.value = 'overview'
      router.replace({ path: '/student', query: { tab: 'overview' } })
      ElMessage.warning('请先选择课程')
      selectorVisible.value = true
      return
    }
    activeTab.value = target
    loadCourseDocuments()
    return
  }
  if (LEARNING_TABS.includes(target) && !ensureContext()) {
    activeTab.value = 'overview'
    router.replace({ path: '/student', query: { tab: 'overview' } })
    ElMessage.warning('请先选择课程和学习资料')
    selectorVisible.value = true
    return
  }
  activeTab.value = target
  if (target === 'practice') loadPracticeTab()
}

function onTabChange(tab) {
  enterTab(tab)
  if (activeTab.value === tab) syncUrlToContext()
}

// selfSync 标记本次 URL 变化是 watcher 内部「补参回声」：仅当补参会真正改变 URL 时才置位，
// 回声触发时跳过 enterTab；选择器确认等其他来源的 URL 变化不置位，保持原有重载语义。
let selfSync = false
watch(
  () => [route.query.tab, route.query.course_id, route.query.document_id],
  ([tab, cid, did]) => {
    if (selfSync) {
      selfSync = false
      return
    }
    if (cid && did) store.setLearningContext({ courseId: cid, documentId: did })
    // URL 不带 tab（如侧边栏「学习总览」跳 /student）：复位到 overview，
    // 否则内部 activeTab 停留在旧 Tab，表现为点击「学习总览」无响应
    if (!tab) {
      if (activeTab.value !== 'overview') enterTab('overview')
      return
    }
    enterTab(tab)
    const urlCid = String(route.query.course_id || '')
    const urlDid = String(route.query.document_id || '')
    if (
      activeTab.value === tab &&
      (urlCid !== String(currentCourseId.value || '') || urlDid !== String(currentDocumentId.value || ''))
    ) {
      selfSync = true
      syncUrlToContext()
    }
  }
)

onMounted(async () => {
  // 先等课程列表就绪再进入 Tab：这样 isMyCourse() 才能可靠判断 URL 里的 course_id
  // 是否属于我（书签/外链里的旧课程 id 会被拦下并引导去课程中心加入）
  await store.fetchCourses().catch(() => {})
  // 修复：URL 带 course_id/document_id 深链进入「学习总览」时，先同步学习上下文
  if (route.query.course_id && route.query.document_id) {
    store.setLearningContext({ courseId: route.query.course_id, documentId: route.query.document_id })
  }
  enterTab(route.query.tab || 'overview')
  if (currentCourseId.value) store.fetchDocuments(currentCourseId.value).catch(() => {})
})

// ===================== 课程文档（在线阅读入口） =====================
const docFilter = ref('')
const docLoading = ref(false)
// 阅读进度存在浏览器本地（见 utils/readingProgress.js），仅用于列表上的续读提示
const docProgress = reactive({})

const filteredDocuments = computed(() => {
  const list = store.learningContext.documentList || []
  const q = docFilter.value.trim().toLowerCase()
  return q ? list.filter((d) => String(d.file_name).toLowerCase().includes(q)) : list
})

function fmtDocSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 本地阅读进度只用于列表上的「继续阅读」提示，不参与任何业务判断 */
function syncDocProgress(list) {
  Object.keys(docProgress).forEach((k) => delete docProgress[k])
  ;(list || []).forEach((d) => {
    const saved = getReadingProgress(d.doc_id)
    if (saved) docProgress[d.doc_id] = saved
  })
}

// 文档列表变化（切换课程 / 首次加载）时同步一次进度
watch(() => store.learningContext.documentList, syncDocProgress, { immediate: true })

async function loadCourseDocuments() {
  const cid = currentCourseId.value
  if (!cid) return
  docLoading.value = true
  try {
    await store.fetchDocuments(cid) // 列表写入 store，进度由上面的 watcher 同步
  } catch (e) {
    ElMessage.warning(`文档列表加载失败：${e.message}`)
  } finally {
    docLoading.value = false
  }
}

function readDocument(doc) {
  router.push({
    name: 'reader',
    params: { docId: String(doc.doc_id) },
    query: { course_id: String(doc.course_id ?? currentCourseId.value), from: 'student' },
  })
}

/** 下载原文件：内容接口需要 JWT，取回字节后用 Blob 触发下载 */
async function downloadDocument(doc) {
  try {
    const buffer = await fetchDocumentBuffer(doc.doc_id)
    const url = URL.createObjectURL(new Blob([buffer]))
    const a = document.createElement('a')
    a.href = url
    a.download = doc.file_name || `document-${doc.doc_id}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  } catch (e) {
    ElMessage.error(`下载失败：${e.message}`)
  }
}

/** 当前上下文里直接开始阅读（上下文条按钮） */
function readCurrentDocument() {
  if (!currentCourseId.value || !currentDocumentId.value) return
  router.push({
    name: 'reader',
    params: { docId: String(currentDocumentId.value) },
    query: { course_id: String(currentCourseId.value), from: 'student' },
  })
}

// ===================== 图谱浏览 =====================
const browseGraphRef = ref(null)
const searchText = ref('')
const stats = ref(null)
const drawerVisible = ref(false)
const drawerNode = ref(null)
const drawerRelated = ref([])
const drawerPredecessors = ref([])
const drawerExpanded = ref(false)

// ===================== 学习记录（掌握标记，P7：按文档作用域缓存跨 tab 统一） =====================
const masteredCache = reactive({}) // { "courseId:documentId": [kpId, ...] }
const masteryLoading = ref(false)
const masteredKpIds = computed(() => masteredCache[ctxKey.value] || [])
const drawerMastered = computed(() =>
  drawerNode.value ? masteredKpIds.value.includes(String(drawerNode.value.id)) : false
)

async function onToggleMastery() {
  const node = drawerNode.value
  if (!node || !ctxKey.value) return
  const kpId = String(node.id)
  const key = ctxKey.value
  const cid = currentCourseId.value
  const did = currentDocumentId.value
  const isMastered = (masteredCache[key] || []).includes(kpId)
  masteryLoading.value = true
  try {
    if (isMastered) {
      await api.unmarkMastered(cid, did, kpId)
      masteredCache[key] = (masteredCache[key] || []).filter((id) => id !== kpId)
      ElMessage.success('已取消掌握标记')
    } else {
      await api.markMastered(cid, did, kpId)
      masteredCache[key] = [...(masteredCache[key] || []), kpId]
      ElMessage.success('已标记为掌握')
    }
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  } finally {
    masteryLoading.value = false
  }

  // 掌握状态变更后刷新「当前/推荐」状态，推进学习进度
  loadPathData()
}

// ===================== 收藏（收藏 = 个人知识点书签，独立于学习状态，按文档作用域缓存跨 tab 统一） =====================
const favoriteCache = reactive({}) // { "courseId:documentId": [kpId, ...] }
const favoriteLoading = ref(false)
const drawerFavorited = computed(() =>
  drawerNode.value ? isFavorited(String(drawerNode.value.id)) : false
)

async function loadFavorites() {
  const key = ctxKey.value
  const cid = currentCourseId.value
  if (!key || !cid) return []
  try {
    const res = await api.getFavorites(cid, currentDocumentId.value)
    favoriteCache[key] = (res.items || []).map((f) => f.kp_id)
  } catch (e) {
    favoriteCache[key] = []
  }
  return favoriteCache[key]
}

function isFavorited(kpId) {
  if (!ctxKey.value || kpId == null) return false
  return (favoriteCache[ctxKey.value] || []).includes(String(kpId))
}

// 核心收藏/取消逻辑（详情抽屉 / 学习路径 / QA 来源 / 收藏夹全部走这里，统一维护收藏状态）
async function toggleFavorite(kpId) {
  const key = ctxKey.value
  const cid = currentCourseId.value
  const did = currentDocumentId.value
  if (!key || !cid || kpId == null) return
  const kp = String(kpId)
  if (isFavorited(kp)) {
    await api.removeFavorite(cid, did, kp)
    favoriteCache[key] = (favoriteCache[key] || []).filter((id) => id !== kp)
    ElMessage.success('已取消收藏')
  } else {
    await api.addFavorite(cid, did, kp)
    favoriteCache[key] = [...(favoriteCache[key] || []), kp]
    ElMessage.success('已收藏')
  }
}

// 轻量入口（QA 来源卡 / 学习路径 / 收藏夹）：无独立 loading，仅提示 + 状态同步
async function toggleFavoriteSafe(kpId) {
  try {
    await toggleFavorite(kpId)
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

// 详情抽屉的收藏按钮（带 loading 态）
async function onToggleFavorite() {
  const node = drawerNode.value
  if (!node || !ctxKey.value || node.id == null) return
  favoriteLoading.value = true
  try {
    await toggleFavorite(node.id)
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  } finally {
    favoriteLoading.value = false
  }
}

function refreshBrowse() {
  browseGraphRef.value?.refresh()
}
function onNodeClick(node, info) {
  drawerNode.value = node
  drawerRelated.value = [...(info?.successors || []), ...(info?.related || [])]
  drawerPredecessors.value = info?.predecessors || []
  drawerExpanded.value = !!info?.expanded
  drawerVisible.value = true
}

// P6：展开/收起当前节点的相关知识（局部展开模式）
function onExpandToggle() {
  if (!drawerNode.value || !browseGraphRef.value) return
  drawerExpanded.value = browseGraphRef.value.toggleExpand(String(drawerNode.value.id))
}

// P6：点击「相关知识」项 → 图谱聚焦并联动更新抽屉
function onJumpToNode(node) {
  if (!node || !browseGraphRef.value) return
  browseGraphRef.value.selectNode(String(node.id))
}

// 学习上下文建立/切换时的数据加载统一收敛到下方 watch(ctxKey)，不再按 courseId 单独 watch

// ===================== 智能问答 =====================
const question = ref('')
const asking = ref(false)
const messages = ref([])
const chatBoxRef = ref(null)

async function sendQuestion() {
  const q = question.value.trim()
  if (!q || asking.value) return
  question.value = ''
  messages.value.push({ role: 'user', content: q })
  asking.value = true
  scrollChatToBottom()
  try {
    const res = await api.ask(q, currentCourseId.value, currentDocumentId.value)
    const sources = (res.sources || []).map(normalizeSource)
    // P8：区分「检索成功但生成失败」（LLM 降级文案）与「检索空结果」，统一前端状态
    messages.value.push({
      role: 'ai',
      content: res.answer || '（无回答）',
      sources,
      error: isLlmError(res.answer),
    })
  } catch (e) {
    // 网络 / 接口整体异常
    messages.value.push({ role: 'ai', content: '', error: true, sources: [] })
  } finally {
    asking.value = false
    scrollChatToBottom()
  }
}

// LLM 生成失败判定：后端在生成异常时返回固定前缀的降级文案（不改后端契约，前端识别）
function isLlmError(answer) {
  return typeof answer === 'string' && answer.indexOf('问答服务暂时不可用') !== -1
}

function scrollChatToBottom() {
  nextTick(() => {
    if (chatBoxRef.value) {
      chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
    }
  })
}

function clearChat() {
  messages.value = []
}

// 引用来源归一化：后端返回结构化对象；兼容旧字符串格式（[类别] 名称: 描述）
function normalizeSource(s) {
  if (typeof s !== 'string') return s || {}
  const m = s.match(/^\[(.+?)\]\s*(.+?)(?::\s*([\s\S]*))?$/)
  if (m) return { category: m[1], name: m[2].trim(), description: (m[3] || '').trim() }
  return { category: '', name: s, description: '' }
}

// 左栏「相关知识点」= 最近一条 AI 回答的引用来源
const relatedKps = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]
    if (m.role === 'ai' && m.sources?.length) return m.sources
  }
  return []
})

// 点击引用/相关知识点 → 跳图谱浏览并高亮 + 打开详情（问答 → 详情 → 图谱聚焦闭环）
function jumpToKp(kp) {
  if (!kp || !kp.name) return
  activeTab.value = 'browse'
  // QA 来源为 {kp_id, name, category, description}，构造详情所需节点对象
  drawerNode.value = {
    id: kp.kp_id ?? kp.name,
    label: kp.name,
    description: kp.description || '',
    properties: { category: kp.category || '', is_manual: false, confidence: null },
  }
  drawerRelated.value = []
  drawerPredecessors.value = []
  drawerExpanded.value = false
  drawerVisible.value = true
  // 始终以 kp_id 定位（与详情抽屉的 drawerNode.id 同一来源），避免依赖名称映射
  highlightPathNodes.value = [String(kp.kp_id ?? kp.name)]
}

// ===================== 收藏夹 Tab（学生个人知识点书签） =====================
const favSearch = ref('')
const favCategory = ref('')
const favNodes = ref([])
const favLoading = ref(false)
const favLoaded = ref(false)

const favTotal = computed(() => (favoriteCache[ctxKey.value] || []).length)

const favoriteList = computed(() => {
  const ids = favoriteCache[ctxKey.value] || []
  const nodeById = new Map(favNodes.value.map((n) => [String(n.id), n]))
  const kw = favSearch.value.trim().toLowerCase()
  const cat = favCategory.value
  return ids
    .map((id) => nodeById.get(String(id)))
    .filter(Boolean)
    .filter((n) => !cat || (n.properties?.category || '') === cat)
    .filter(
      (n) =>
        !kw ||
        String(n.label).toLowerCase().includes(kw) ||
        String(n.description || '').toLowerCase().includes(kw)
    )
})

async function loadFavoritesTab() {
  if (!ctxKey.value) {
    favNodes.value = []
    favLoaded.value = false
    return
  }
  favLoading.value = true
  try {
    // favoriteCache 由 loadFavorites 统一写入（同一文档作用域），此处仅取图谱节点名/描述
    const graph = await api.getGraphV1(currentCourseId.value, currentDocumentId.value, { limit: 800 })
    favNodes.value = graph.nodes || []
    favLoaded.value = true
  } catch (e) {
    ElMessage.error(`收藏夹加载失败：${e.message}`)
    favLoaded.value = true
  } finally {
    favLoading.value = false
  }
}

// 收藏夹「查看知识点」→ 复用 P7 统一定位：切图谱 + kp_id 高亮 + 居中 + 打开详情
function viewFavorite(node) {
  if (!node || node.id == null) return
  locateKnowledgePoint(node)
}

function goBrowse() {
  activeTab.value = 'browse'
}

// ===================== 学习路径推荐 =====================
const masteredText = ref('')
const recommendations = ref([])
const recommendLoading = ref(false)
const recommendChecked = ref(false)

const targetKnowledge = ref('')
const paths = ref([])
const pathLoading = ref(false)
const pathChecked = ref(false)
const pathFallback = ref(false)
const pathTargetNode = ref(null)
const pathRelated = ref([])
const pathReason = ref('')
const highlightPathNodes = ref([])

const prereqName = ref('')
const prereqResult = ref(null)
const prereqLoading = ref(false)

async function doRecommend() {
  const mastered = masteredText.value
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  recommendLoading.value = true
  try {
    const res = await api.recommendNext(mastered, currentCourseId.value, currentDocumentId.value)
    recommendations.value = res.recommendations || []
    recommendChecked.value = true
  } catch (e) {
    ElMessage.error(`推荐失败：${e.message}`)
  } finally {
    recommendLoading.value = false
  }
}

async function doPathToTarget() {
  if (!targetKnowledge.value.trim()) {
    ElMessage.warning('请输入目标知识点')
    return
  }
  pathLoading.value = true
  try {
    const res = await api.pathToTarget(targetKnowledge.value.trim(), currentCourseId.value, currentDocumentId.value)
    paths.value = res.paths || []
    pathFallback.value = !!res.fallback
    pathTargetNode.value = res.target_node || null
    pathRelated.value = res.related || []
    pathReason.value = res.reason || ''
    pathChecked.value = true
    // 有真实路径时，高亮到图谱（对齐课程 + 切到浏览 Tab）
    if (res.paths && res.paths.length && ctxKey.value) {
      highlightPathNodes.value = res.paths[0].map((s) => s.name)
      activeTab.value = 'browse'
      ElMessage.success('已生成路径，正在图谱中高亮')
    } else {
      highlightPathNodes.value = []
    }
  } catch (e) {
    ElMessage.error(`生成失败：${e.message}`)
  } finally {
    pathLoading.value = false
  }
}

async function doQueryPrereqs() {
  if (!prereqName.value.trim()) {
    ElMessage.warning('请输入知识点名称')
    return
  }
  prereqLoading.value = true
  prereqResult.value = null
  try {
    prereqResult.value = await api.getPrerequisites(prereqName.value.trim(), currentCourseId.value, currentDocumentId.value)
  } catch (e) {
    ElMessage.error(`查询失败：${e.message}`)
  } finally {
    prereqLoading.value = false
  }
}

function categoryTagType(category) {
  const map = { 概念: 'primary', 定理: 'danger', 公式: 'warning', 方法: 'success' }
  return map[category] || 'info'
}

// ===================== 学习路径视觉层级（P5：已掌握/当前/推荐/未学习） =====================
const pathGraphNodes = ref([])
const pathGraphEdges = ref([])
const pathRecs = ref([])
const pathDataLoading = ref(false)
const pathDataChecked = ref(false)

// P7：展示层唯一状态源（跨路径/图谱/详情/问答统一）
// 「已掌握」来自 masteredCache（后端 getProgress，按文档作用域缓存）；
// 「当前学习」「推荐学习」来自 recommendNext 结果映射的 kp_id。
const currentKnowledgePointId = ref(null) // 当前学习知识点 kp_id
const recommendedKnowledgePointIds = ref([]) // 推荐学习知识点 kp_id 列表
const pathMasteredIds = computed(() => masteredCache[ctxKey.value] || [])

const currentKnowledgePoint = computed(() => {
  const id = currentKnowledgePointId.value
  if (!id) return null
  return pathGraphNodes.value.find((n) => String(n.id) === id) || null
})

// 推荐项 kp_id → recommendNext 原始条目（取 reason）
const recById = computed(() => {
  const nameToId = new Map(pathGraphNodes.value.map((n) => [n.label, String(n.id)]))
  const map = new Map()
  for (const r of pathRecs.value) {
    const id = nameToId.get(r.name)
    if (id) map.set(id, r)
  }
  return map
})

// P7：图谱状态仅在当前文档上下文内生效（避免跨文档残留）
const browseCurrentKpId = computed(() => (ctxKey.value ? currentKnowledgePointId.value : null))
const browseRecommendedKpIds = computed(() => (ctxKey.value ? recommendedKnowledgePointIds.value : []))

// 详情抽屉的节点学习状态（当前学习 / 推荐学习）
const drawerLearningState = computed(() => {
  const id = drawerNode.value ? String(drawerNode.value.id) : ''
  if (!id) return ''
  // 已掌握节点不再显示「当前/推荐」状态标签
  if (masteredKpIds.value.includes(id)) return ''
  if (currentKnowledgePointId.value === id) return 'current'
  if (recommendedKnowledgePointIds.value.includes(id)) return 'recommended'
  return ''
})

const STATE_META = {
  mastered: { label: '已掌握', glyph: '✓' },
  current: { label: '当前学习', glyph: '●' },
  recommended: { label: '推荐学习', glyph: '★' },
  unlearned: { label: '未学习', glyph: '○' },
}

async function loadPathData() {
  const key = ctxKey.value
  const cid = currentCourseId.value
  if (!key || !cid) {
    pathGraphNodes.value = []
    pathGraphEdges.value = []
    pathRecs.value = []
    currentKnowledgePointId.value = null
    recommendedKnowledgePointIds.value = []
    pathDataChecked.value = false
    return
  }
  pathDataLoading.value = true
  try {
    const [graph, progress] = await Promise.all([
      api.getGraphV1(cid, currentDocumentId.value, { limit: 800 }),
      api.getProgress(cid, currentDocumentId.value),
    ])
    if (ctxKey.value !== key) return // 上下文已切换，丢弃过期结果
    pathGraphNodes.value = graph.nodes || []
    pathGraphEdges.value = graph.edges || []
    masteredCache[key] = progress.mastered_kp_ids || []

    // 已掌握 kp_id → 名称（recommendNext 入参需名称）
    const byId = new Map(pathGraphNodes.value.map((n) => [String(n.id), n]))
    const nameToId = new Map(pathGraphNodes.value.map((n) => [n.label, String(n.id)]))
    const masteredNames = (progress.mastered_kp_ids || [])
      .map((id) => byId.get(String(id))?.label)
      .filter(Boolean)
    const res = await api.recommendNext(masteredNames, cid, currentDocumentId.value)
    if (ctxKey.value !== key) return // 第二次 await 后再校验
    pathRecs.value = res.recommendations || []

    // P7：将 recommendNext 结果映射为 kp_id，建立展示层唯一状态源
    currentKnowledgePointId.value = pathRecs.value[0]
      ? nameToId.get(pathRecs.value[0].name) || null
      : null
    recommendedKnowledgePointIds.value = pathRecs.value
      .slice(1)
      .map((r) => nameToId.get(r.name))
      .filter(Boolean)
  } catch (e) {
    if (ctxKey.value !== key) return
    pathRecs.value = []
    currentKnowledgePointId.value = null
    recommendedKnowledgePointIds.value = []
  } finally {
    pathDataLoading.value = false
    pathDataChecked.value = true
  }
}

// ===================== 做题练习（Scope A：单选/多选/判断；出题接口不含答案） =====================
const practiceSubTab = ref('doing')      // doing=答题 / wrong=错题本 / fav=收藏题目
const practiceScope = ref('document')    // document=本学习资料 / course=整门课程
const practiceQType = ref('')
const practiceCount = ref(10)
const practiceKpId = ref('')
const practiceKpOptions = ref([])        // 图谱节点（含 id/label/description），供选知识点与跳转
const practiceList = ref([])
const practiceIndex = ref(0)
const practiceLoading = ref(false)
const practiceSubmitting = ref(false)
/** 智能推荐（P2）：出题模式 + 最近一次的推荐元信息（分桶/掌握度证据/是否回填） */
const PRACTICE_MODES = [
  { value: 'mixed', label: '分层组卷' },
  { value: 'weak', label: '薄弱强化' },
  { value: 'review', label: '复习巩固' },
  { value: 'new', label: '路径新知识' },
  { value: 'advanced', label: '进阶提升' },
  { value: 'random', label: '随机练习' },
]
const practiceMode = ref('mixed')
const practiceRecommendMeta = ref(null)
const practiceRecommendHint = computed(() => {
  const meta = practiceRecommendMeta.value
  if (!meta) return ''
  const label = PRACTICE_MODES.find((m) => m.value === meta.mode)?.label || meta.mode
  const buckets = Object.entries(meta.buckets || {})
    .filter(([k, v]) => v > 0 && k !== 'quota_relaxed')
    .map(([k, v]) => `${({ weak: '薄弱', review: '复习', new: '新知识', advanced: '进阶', filled: '回填', random: '随机' })[k] || k} ${v}`)
    .join(' / ')
  const parts = [`智能推荐（${label}）`, buckets && `构成：${buckets}`,
    meta.mastery_available ? '依据：掌握度+作答历史' : '依据：证据不足（按摸底推荐）']
  if (meta.bucket_empty) parts.push('该类型当前无题，已用其他类型回填')
  if (!meta.graph_available) parts.push('图谱不可用（未纳入知识点重要性）')
  return parts.filter(Boolean).join(' · ')
})
const practiceStats = ref(null)
const practiceStatsLoading = ref(false)
const practiceAnswers = reactive({})     // question_id -> 单选/判断为字符串，多选为数组
const practiceResults = reactive({})     // question_id -> 提交后的判分结果（含正确答案与解析）
const practiceFavoriteIds = ref([])      // 我已收藏的 question_id（本课程）

const wrongList = ref([])
const wrongLoading = ref(false)
const myQuestionFavs = ref([])
const myQuestionFavLoading = ref(false)

const currentPracticeQuestion = computed(() => practiceList.value[practiceIndex.value] || null)

/** 答案展示：单选→键；多选→"A、C"；判断→正确/错误；填空→各空参考答案；解答→文本 */
function formatAnswer(answer) {
  if (answer === null || answer === undefined || answer === '') return '—'
  // 填空题作答/答案形如 {"1":"浮点数","2":"0.5"}（修复此前显示成 [object Object] 的问题）
  if (typeof answer === 'object' && !Array.isArray(answer)) {
    const keys = Object.keys(answer).sort((a, b) => Number(a) - Number(b))
    const parts = keys.map((k) => `第${k}空：${String(answer[k] ?? '').trim() || '（未填）'}`)
    return parts.length ? parts.join('；') : '—'
  }
  if (Array.isArray(answer)) return answer.length ? answer.join('、') : '—'
  if (answer === true || String(answer) === 'true') return '正确'
  if (answer === false || String(answer) === 'false') return '错误'
  return String(answer)
}

/** 新建一道题的作答容器：多选=数组，填空=各空对象，其余=字符串 */
function emptyAnswerFor(q) {
  if (!q) return ''
  if (q.q_type === 'MULTI') return []
  if (q.q_type === 'FILL') {
    const blanks = {}
    ;(q.options || []).forEach((b) => {
      blanks[b.key] = ''
    })
    return blanks
  }
  return ''
}

/** 主观题提交后进入待批改：结果区文案 / 图标 / 配色（与客观题共用同一容器） */
function resultText(qid) {
  const r = practiceResults[qid] || {}
  if (r.pending) return '已提交，等待教师批改'
  return r.is_correct ? '回答正确' : '回答错误'
}

function resultIcon(qid) {
  const r = practiceResults[qid] || {}
  if (r.pending) return Clock
  return r.is_correct ? CircleCheckFilled : WarningFilled
}

function resultClass(qid) {
  const r = practiceResults[qid]
  if (!r) return ''
  if (r.pending) return 'pending'
  return r.is_correct ? 'ok' : 'bad'
}

function isPracticeFavorited(q) {
  return !!q && practiceFavoriteIds.value.includes(q.question_id)
}

/** 知识点下拉数据：复用文档图谱节点（含描述，便于「查看知识点」直接打开详情抽屉） */
async function ensurePracticeKpOptions() {
  if (!currentCourseId.value || !currentDocumentId.value) {
    practiceKpOptions.value = []
    return practiceKpOptions.value
  }
  if (practiceKpOptions.value.length) return practiceKpOptions.value
  try {
    const g = await api.getGraphV1(currentCourseId.value, currentDocumentId.value, { limit: 500 })
    practiceKpOptions.value = g.nodes || []
  } catch {
    practiceKpOptions.value = []
  }
  return practiceKpOptions.value
}

async function loadPracticeStats() {
  if (!currentCourseId.value) {
    practiceStats.value = null
    return
  }
  practiceStatsLoading.value = true
  try {
    practiceStats.value = await api.getPracticeStats({
      course_id: currentCourseId.value,
      document_id: practiceScope.value === 'document' ? currentDocumentId.value : undefined,
    })
  } catch {
    practiceStats.value = null
  } finally {
    practiceStatsLoading.value = false
  }
}

/** 出题并重置作答/判定状态（学生接口不下发答案，判分只在提交后返回） */
async function startPractice() {
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择课程和学习资料')
    return
  }
  practiceLoading.value = true
  try {
    const data = await api.getPracticeQuestions({
      course_id: currentCourseId.value,
      document_id: practiceScope.value === 'document' ? currentDocumentId.value : undefined,
      kp_id: practiceKpId.value || undefined,
      q_type: practiceQType.value || undefined,
      count: practiceCount.value,
    })
    applyPracticeQuestions(data.items || [])
    practiceRecommendMeta.value = null          // 普通出题没有推荐元信息
    if (!practiceList.value.length) {
      ElMessage.info('该范围内暂无题目，请更换出题范围或联系教师添加题目')
    }
  } catch (e) {
    ElMessage.error(`出题失败：${e.message}`)
  } finally {
    practiceLoading.value = false
  }
}

/** 智能推荐出题（P2）：按学情选卷（掌握度/遗忘/难度适配/错题/图谱重要性…） */
async function startSmartPractice() {
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择课程和学习资料')
    return
  }
  practiceLoading.value = true
  try {
    const data = await api.recommendQuestions({
      course_id: currentCourseId.value,
      document_id: practiceScope.value === 'document' ? currentDocumentId.value : undefined,
      kp_id: practiceKpId.value || undefined,
      q_type: practiceQType.value || undefined,
      count: practiceCount.value,
      mode: practiceMode.value,
    })
    applyPracticeQuestions(data.items || [])
    practiceRecommendMeta.value = data.meta || null
    const label = PRACTICE_MODES.find((m) => m.value === practiceMode.value)?.label
    if (data.count) ElMessage.success(`已按「${label}」推荐 ${data.count} 道题`)
    else ElMessage.info('该范围内暂无可用题目')
  } catch (e) {
    ElMessage.error(`智能推荐失败：${e.message}`)
  } finally {
    practiceLoading.value = false
  }
}

/** 把一批题目装载为当前练习（出题 / 智能推荐共用：清空上一组作答与判定，避免串题） */
function applyPracticeQuestions(items) {
  practiceList.value = items
  practiceIndex.value = 0
  Object.keys(practiceAnswers).forEach((k) => delete practiceAnswers[k])
  Object.keys(practiceResults).forEach((k) => delete practiceResults[k])
  practiceList.value.forEach((q) => {
    practiceAnswers[q.question_id] = emptyAnswerFor(q)
  })
  practiceFavoriteIds.value = practiceList.value
    .filter((q) => q.is_favorited).map((q) => q.question_id)
  practiceSubTab.value = 'doing'
}

/** 按学习路径推荐的知识点出题（推荐 → 练题闭环） */
async function practiceByRecommendation() {
  if (!currentCourseId.value || !currentDocumentId.value) {
    ElMessage.warning('请先选择课程和学习资料')
    return
  }
  try {
    const nodes = await ensurePracticeKpOptions()
    const progress = await api.getProgress(currentCourseId.value, currentDocumentId.value)
    const nameOf = (kpId) => nodes.find((n) => String(n.id) === String(kpId))?.label
    const mastered = (progress.mastered_kp_ids || []).map(nameOf).filter(Boolean)
    const recs = await api.recommendNext(mastered, currentCourseId.value, currentDocumentId.value)
    const first = (recs || [])[0]
    if (!first || !first.name) {
      ElMessage.info('暂无可推荐的知识点，请先标记已掌握的知识点或查看学习路径')
      return
    }
    const target = nodes.find((n) => n.label === first.name)
    practiceKpId.value = target ? target.id : ''
    if (!target) {
      ElMessage.warning(`推荐知识点「${first.name}」未在当前图谱中定位，将按其他条件出题`)
    } else {
      ElMessage.success(`已按推荐知识点「${first.name}」出题`)
    }
    await startPractice()
  } catch (e) {
    ElMessage.error(`推荐出题失败：${e.message}`)
  }
}

/** 提交当前题：服务端判分 + 落答题记录，返回正确答案与解析后展示 */
async function submitCurrent() {
  const q = currentPracticeQuestion.value
  if (!q) return
  const answer = practiceAnswers[q.question_id]
  // 概念：填空题答案是「各空对象」，需所有空位都空才算未作答
  const isBlankObject = answer && typeof answer === 'object' && !Array.isArray(answer)
  const empty = answer === undefined || answer === null || answer === '' ||
    (Array.isArray(answer) && !answer.length) ||
    (isBlankObject && !Object.values(answer).some((v) => String(v ?? '').trim()))
  if (empty) {
    ElMessage.warning(q.q_type === 'FILL' ? '请至少填写一个空位' : '请先作答再提交')
    return
  }
  practiceSubmitting.value = true
  try {
    const data = await api.submitAnswer(q.question_id, answer)
    practiceResults[q.question_id] = data
    if (data.pending) ElMessage.success('已提交，等待教师批改')
    else if (data.is_correct) ElMessage.success('回答正确 🎉')
    else ElMessage.warning('回答错误，看看解析再试')
    loadPracticeStats()
  } catch (e) {
    ElMessage.error(`提交失败：${e.message}`)
  } finally {
    practiceSubmitting.value = false
  }
}

/** 收藏 / 取消收藏题目（幂等，后端保证不重复插入） */
async function togglePracticeFavorite(q) {
  if (!q || !currentCourseId.value) return
  try {
    if (isPracticeFavorited(q)) {
      await api.unfavoriteQuestion(currentCourseId.value, q.question_id)
      practiceFavoriteIds.value = practiceFavoriteIds.value.filter((id) => id !== q.question_id)
      myQuestionFavs.value = myQuestionFavs.value.filter((x) => x.question_id !== q.question_id)
      ElMessage.success('已取消收藏')
    } else {
      await api.favoriteQuestion(currentCourseId.value, q.question_id)
      practiceFavoriteIds.value = [...practiceFavoriteIds.value, q.question_id]
      ElMessage.success('已收藏题目')
    }
    loadPracticeStats()
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

/** 错题本：按题取最近一次答错（含正确答案/解析/知识点名称） */
async function loadWrongBook() {
  if (!currentCourseId.value) {
    wrongList.value = []
    return
  }
  wrongLoading.value = true
  try {
    const data = await api.getWrongBook({
      course_id: currentCourseId.value,
      document_id: practiceScope.value === 'document' ? currentDocumentId.value : undefined,
    })
    wrongList.value = data.items || []
  } catch (e) {
    wrongList.value = []
    ElMessage.warning(`错题本加载失败：${e.message}`)
  } finally {
    wrongLoading.value = false
  }
}

/** 我收藏的题目（本课程） */
async function loadMyQuestionFavorites() {
  if (!currentCourseId.value) {
    myQuestionFavs.value = []
    return
  }
  myQuestionFavLoading.value = true
  try {
    const data = await api.getQuestionFavList(currentCourseId.value)
    myQuestionFavs.value = data.items || []
    // 同一课程内「我的收藏」是全量集合，可安全用于覆盖收藏标记
    practiceFavoriteIds.value = myQuestionFavs.value.map((q) => q.question_id)
  } catch (e) {
    myQuestionFavs.value = []
    ElMessage.warning(`收藏题目加载失败：${e.message}`)
  } finally {
    myQuestionFavLoading.value = false
  }
}

/** 从错题本 / 收藏题目直接重做：载入单题并切回答题子页 */
function redoQuestion(q) {
  if (!q) return
  practiceList.value = [{ ...q }]
  practiceIndex.value = 0
  practiceAnswers[q.question_id] = emptyAnswerFor(q)
  delete practiceResults[q.question_id]
  practiceSubTab.value = 'doing'
}

/** 查看该题关联的知识点（切到图谱浏览并打开详情抽屉） */
function viewPracticeKp(q) {
  const node = practiceKpOptions.value.find((n) => String(n.id) === String(q?.kp_id))
  if (!node) {
    ElMessage.info('该题未关联图谱知识点')
    return
  }
  locateKnowledgePoint(node)
}

/** 进入练习 Tab / 切换上下文时统一刷新（统计 + 错题本 + 收藏题目 + 知识点下拉） */
async function loadPracticeTab() {
  ensurePracticeKpOptions()
  await Promise.all([loadPracticeStats(), loadWrongBook(), loadMyQuestionFavorites()])
}

// 子页切换：按需刷新错题本 / 收藏题目
watch(practiceSubTab, (sub) => {
  if (sub === 'wrong') loadWrongBook()
  else if (sub === 'fav') loadMyQuestionFavorites()
})

// 学习上下文切换：清空文档级学习状态，并加载对应文档作用域的数据
watch(ctxKey, (key) => {
  currentKnowledgePointId.value = null
  recommendedKnowledgePointIds.value = []
  // 练习上下文变化即失效：清空作答/判定/题目与知识点下拉
  practiceList.value = []
  practiceIndex.value = 0
  practiceKpId.value = ''
  practiceKpOptions.value = []
  Object.keys(practiceAnswers).forEach((k) => delete practiceAnswers[k])
  Object.keys(practiceResults).forEach((k) => delete practiceResults[k])
  if (key) {
    loadPathData()
    loadFavorites()
    loadFavoritesTab()
    loadPracticeTab()
  } else {
    pathGraphNodes.value = []
    pathGraphEdges.value = []
    pathRecs.value = []
    favNodes.value = []
    favLoaded.value = false
    pathDataChecked.value = false
    practiceStats.value = null
    wrongList.value = []
    myQuestionFavs.value = []
    practiceFavoriteIds.value = []
    practiceRecommendMeta.value = null
  }
}, { immediate: true }) // 挂载时若已有持久化学习上下文（如课程中心往返/刷新），立即加载数据

const pathProgress = computed(() => {
  const total = pathGraphNodes.value.length
  const mastered = pathMasteredIds.value.length
  const pct = total ? Math.round((mastered / total) * 100) : 0
  return { total, mastered, pct, current: currentKnowledgePoint.value?.label || '' }
})

// 基于图谱 PRECEDES 边 + 统一学习状态，构建「已掌握 → 当前 → 推荐 → 未学习」展示链
const pathChain = computed(() => {
  const nodes = pathGraphNodes.value
  if (!nodes.length) return []

  const byId = new Map(nodes.map((n) => [String(n.id), n]))
  const byName = new Map(nodes.map((n) => [n.label, n]))
  const masteredSet = new Set(pathMasteredIds.value.map(String))

  const succ = new Map() // name -> [name]（PRECEDES：source 是 target 的前置）
  const pred = new Map()
  for (const e of pathGraphEdges.value) {
    if (e.type !== 'PRECEDES') continue
    const s = byId.get(String(e.source))?.label
    const t = byId.get(String(e.target))?.label
    if (!s || !t || s === t) continue
    if (!succ.has(s)) succ.set(s, [])
    succ.get(s).push(t)
    if (!pred.has(t)) pred.set(t, [])
    pred.get(t).push(s)
  }

  const current = currentKnowledgePoint.value
  if (!current) return []

  const segs = []
  const used = new Set()

  // 1) 已掌握：current 的直接前置（且已掌握）
  for (const pname of pred.get(current.label) || []) {
    const node = byName.get(pname)
    if (node && masteredSet.has(String(node.id)) && !used.has(node.id)) {
      used.add(node.id)
      segs.push({ node, state: 'mastered', reason: '' })
    }
  }

  // 2) 当前学习
  if (!used.has(current.id)) {
    used.add(current.id)
    segs.push({ node: current, state: 'current', reason: recById.value.get(current.id)?.reason || '' })
  }

  // 3) 推荐学习：优先取 recommendNext 推荐中 current 的后继，否则取结构后继
  const nexts = succ.get(current.label) || []
  const recIds = recommendedKnowledgePointIds.value
  const rId =
    recIds.find((id) => nexts.includes(byId.get(id)?.label)) ||
    recIds[0] ||
    (nexts[0] ? byName.get(nexts[0])?.id : null)
  if (rId) {
    const rNode = byId.get(String(rId))
    if (rNode && !used.has(rNode.id)) {
      used.add(rNode.id)
      segs.push({
        node: rNode,
        state: 'recommended',
        reason: recById.value.get(rNode.id)?.reason || `前置知识「${current.label}」掌握后即可学习`,
      })
    }
    // 4) 未学习：推荐节点的后继（再进一步）
    const next2 = succ.get(rNode?.label) || []
    for (const n2name of next2) {
      const n2 = byName.get(n2name)
      if (n2 && !used.has(n2.id)) {
        used.add(n2.id)
        segs.push({ node: n2, state: 'unlearned', reason: '' })
        break
      }
    }
  }

  return segs
})

function startLearning() {
  if (!currentKnowledgePoint.value) return
  locateKnowledgePoint(currentKnowledgePoint.value)
  ElMessage.success(`开始学习「${currentKnowledgePoint.value.label}」`)
}

// P7：统一的「定位知识点」：切到图谱 → 打开详情 → 图谱金高亮 + 居中（路径/开始学习复用）
function locateKnowledgePoint(node) {
  if (!node || node.id == null) return
  activeTab.value = 'browse'
  // 打开详情（用 pathGraph 邻接数据，无需等图谱加载完成）
  drawerNode.value = node
  const nb = neighborsOf(node.id)
  drawerRelated.value = [...nb.successors, ...nb.related]
  drawerPredecessors.value = nb.predecessors
  drawerExpanded.value = browseGraphRef.value?.isExpanded(String(node.id)) ?? false
  drawerVisible.value = true
  // 图谱定位：金高亮 + 居中（以 kp_id 定位，与详情抽屉同一来源）
  highlightPathNodes.value = [String(node.id)]
}

// 基于 pathGraph 的客户端一阶邻接（前置/后继/相关），供详情抽屉复用
function neighborsOf(nodeId) {
  const id = String(nodeId)
  const byId = new Map(pathGraphNodes.value.map((n) => [String(n.id), n]))
  const predecessors = []
  const successors = []
  const related = []
  for (const e of pathGraphEdges.value) {
    const s = String(e.source)
    const t = String(e.target)
    if (e.type === 'PRECEDES') {
      if (t === id) predecessors.push(byId.get(s))
      else if (s === id) successors.push(byId.get(t))
    } else {
      if (s === id) related.push(byId.get(t))
      else if (t === id) related.push(byId.get(s))
    }
  }
  return {
    predecessors: predecessors.filter(Boolean),
    successors: successors.filter(Boolean),
    related: related.filter(Boolean),
  }
}

// ===================== 学习总览（学习驾驶舱，P11） =====================
// 统一复用学习状态源（learningContext / masteredCache / favoriteCache / pathRecs /
// currentKnowledgePointId / recommendedKnowledgePointIds），不新建任何重复状态；
// 所有数字均来自后端真实数据（图谱 / 学习进度 / 推荐 / 收藏），无伪造指标。

const overviewTotal = computed(() => pathGraphNodes.value.length)
const overviewMastered = computed(() => pathMasteredIds.value.length)
const overviewPct = computed(() => pathProgress.value.pct)
const overviewFavIds = computed(() => favoriteCache[ctxKey.value] || [])

// 四态计数（互斥、求和 = 知识点总数，颜色仅辅助，字形 + 文本为主信号）
const overviewStateCounts = computed(() => {
  const total = pathGraphNodes.value.length
  const masteredSet = new Set(pathMasteredIds.value.map(String))
  const cur = currentKnowledgePointId.value
  const curIsMastered = cur ? masteredSet.has(String(cur)) : false
  const currentCount = cur && !curIsMastered ? 1 : 0
  const recommendedCount = recommendedKnowledgePointIds.value.filter(
    (id) => !masteredSet.has(String(id))
  ).length
  return {
    total,
    mastered: masteredSet.size,
    current: currentCount,
    recommended: recommendedCount,
    unlearned: Math.max(0, total - masteredSet.size - currentCount - recommendedCount),
  }
})

const overviewCurrentNode = computed(() => currentKnowledgePoint.value)

// 推荐下一步 = recommendNext 结果中排除「当前学习」后的前 4 条（真实推荐逻辑）
const overviewRecommended = computed(() => {
  const curName = overviewCurrentNode.value?.label
  return pathRecs.value.filter((r) => r.name !== curName).slice(0, 4)
})

// 建议重点学习 = recommendNext 推荐结果（含真实 reason，不伪造原因）
const overviewFocusList = computed(() => {
  const nameToId = new Map(pathGraphNodes.value.map((n) => [n.label, String(n.id)]))
  return pathRecs.value.slice(0, 5).map((r) => ({
    kpId: nameToId.get(r.name) || null,
    name: r.name,
    category: r.category,
    description: r.description,
    reason: r.reason,
  }))
})

// 最近收藏 = favoriteCache（API 按收藏时间倒序返回，保持原序）映射到图谱节点
const overviewRecentFavs = computed(() => {
  const byId = new Map(pathGraphNodes.value.map((n) => [String(n.id), n]))
  return overviewFavIds.value
    .slice(0, 5)
    .map((id) => byId.get(String(id)))
    .filter(Boolean)
})

const overviewKpis = computed(() => [
  { label: '知识点总数', value: overviewTotal.value, color: '#4f6ef7', icon: Collection },
  { label: '已掌握', value: overviewMastered.value, color: '#67c23a', icon: Checked },
  { label: '学习进度', value: overviewPct.value + '%', color: '#e6a23c', icon: Odometer },
  { label: '我的收藏', value: overviewFavIds.value.length, color: '#f56c6c', icon: StarFilled },
])

const overviewStateList = computed(() => {
  const c = overviewStateCounts.value
  const pct = (n) => (c.total ? Math.round((n / c.total) * 100) : 0)
  return [
    { key: 'mastered', label: '已掌握', glyph: '✓', count: c.mastered, pct: pct(c.mastered), color: '#67c23a' },
    { key: 'current', label: '当前学习', glyph: '●', count: c.current, pct: pct(c.current), color: '#4f6ef7' },
    { key: 'recommended', label: '推荐学习', glyph: '★', count: c.recommended, pct: pct(c.recommended), color: '#e6a23c' },
    { key: 'unlearned', label: '未学习', glyph: '○', count: c.unlearned, pct: pct(c.unlearned), color: '#c0c4cc' },
  ]
})


// 继续学习 → 复用 P7 统一定位（切图谱 + kp_id 高亮 + 居中 + 打开详情）
function overviewContinue() {
  if (!overviewCurrentNode.value) return
  locateKnowledgePoint(overviewCurrentNode.value)
  ElMessage.success(`继续学习「${overviewCurrentNode.value.label}」`)
}
function overviewGoPath() {
  activeTab.value = 'path'
}
function overviewViewName(name) {
  const node = pathGraphNodes.value.find((n) => n.label === name)
  if (node) locateKnowledgePoint(node)
}
function overviewViewFocus(item) {
  const node = pathGraphNodes.value.find((n) => String(n.id) === String(item.kpId))
  if (node) locateKnowledgePoint(node)
}
function overviewViewFav(node) {
  if (node) locateKnowledgePoint(node)
}
function overviewGoFavorites() {
  activeTab.value = 'favorites'
}
</script>

<style scoped>
/* 顶部标签栏已由左侧菜单接管：隐藏标签头，仅保留面板切换机制。
   > 子选择器限定最外层 tabs，练习页内嵌的 practiceSubTab 标签头不受影响。 */
.main-view-tabs > :deep(.el-tabs__header) {
  display: none;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap; /* 窄屏下自动换行，避免横向溢出 */
}
.stats-text {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}
.graph-card {
  height: 620px;
}
.graph-card :deep(.el-card__body) {
  height: 100%;
  padding: var(--space-3);
}

/* 问答 */
.qa-card {
  max-width: 100%;
}
.qa-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}
.chat-box {
  height: 460px;
  padding: var(--space-3);
  background: var(--color-bg-soft);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.chat-welcome {
  text-align: center;
  color: var(--color-text-secondary);
  margin-top: 120px;
}
.welcome-icon {
  font-size: 44px;
}
.welcome-sub {
  font-size: var(--font-size-label);
}
.msg-row {
  display: flex;
  margin-bottom: var(--space-3);
}
.msg-user {
  justify-content: flex-end;
}
.msg-ai {
  justify-content: flex-start;
}
.msg-bubble {
  max-width: 78%;
  padding: 10px var(--space-4);
  border-radius: var(--radius-lg);
  font-size: var(--font-size-body);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-user .msg-bubble {
  background: var(--color-primary);
  color: var(--text-inverse);
}
.msg-ai .msg-bubble {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
}
.msg-sources {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border);
}
.sources-empty {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
/* AI 回答 Markdown 渲染样式（v-html 注入内容，scoped 下需 :deep） */
.msg-md {
  white-space: normal;
}
.msg-md :deep(p) { margin: 0 0 8px; }
.msg-md :deep(p:last-child) { margin-bottom: 0; }
.msg-md :deep(h1),
.msg-md :deep(h2),
.msg-md :deep(h3),
.msg-md :deep(h4) { margin: 12px 0 6px; font-weight: 600; line-height: 1.4; }
.msg-md :deep(h1) { font-size: 18px; }
.msg-md :deep(h2) { font-size: 16px; }
.msg-md :deep(h3) { font-size: 15px; }
.msg-md :deep(h4) { font-size: 14px; }
.msg-md :deep(ul),
.msg-md :deep(ol) { margin: 6px 0 8px; padding-left: 22px; }
.msg-md :deep(li) { margin: 2px 0; }
.msg-md :deep(code) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; background: #f0f1f5; padding: 1px 5px; border-radius: 4px; }
.msg-md :deep(pre) { margin: 8px 0; padding: 10px 12px; background: #f5f6fa; border: 1px solid #eceef3; border-radius: 8px; overflow-x: auto; }
.msg-md :deep(pre code) { background: none; padding: 0; white-space: pre; }
.msg-md :deep(blockquote) { margin: 8px 0; padding: 4px 10px; border-left: 3px solid var(--color-primary, #7c6cf0); color: #555; }
.msg-md :deep(a) { color: var(--color-primary, #5b8bf4); text-decoration: underline; }
.msg-md :deep(hr) { border: none; border-top: 1px solid #e5e5e5; margin: 10px 0; }
.msg-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-danger);
  font-size: var(--font-size-label);
}
.sources-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-2);
}
.source-card {
  padding: var(--space-2) 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 6px;
  background: var(--color-bg-soft);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.source-card:hover {
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
}
.source-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.source-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-jump {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.source-card:hover .source-jump {
  color: var(--color-primary);
}
.source-desc {
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.typing {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: var(--space-4) var(--space-4);
}
.typing-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-muted);
  animation: blink 1.2s infinite;
}
.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}
.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%,
  100% {
    opacity: 0.25;
  }
  50% {
    opacity: 1;
  }
}
.chat-input {
  display: flex;
  gap: 10px;
  margin-top: var(--space-3);
}
.qa-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.qa-side-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--font-weight-semibold);
  font-size: var(--font-size-body);
  color: var(--color-text-primary);
}
.qa-side :deep(.course-selector) {
  flex-wrap: wrap;
  width: 100%;
}
.qa-side :deep(.course-selector .el-select) {
  width: 100% !important;
}
.kp-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kp-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 10px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.kp-item:hover {
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
}
.kp-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kp-jump {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.kp-item:hover .kp-jump {
  color: var(--color-primary);
}

/* 路径推荐 */
.tip {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
  margin: 0 0 10px;
}
.btn-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: 10px;
  flex-wrap: wrap;
}
.rec-item {
  padding: 10px 0;
  border-bottom: 1px dashed var(--color-border);
}
.rec-name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.rec-desc {
  color: var(--color-text-regular);
  font-size: var(--font-size-label);
  margin: 6px 0 4px;
}
.rec-reason {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}
.path-chain {
  margin-bottom: var(--space-4);
  padding: 10px;
  background: var(--color-bg-soft);
  border-radius: var(--radius-md);
}
.path-index {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-2);
}
.path-steps {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.path-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
}
.step-name {
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-semibold);
}
.path-arrow {
  color: var(--color-success);
  font-weight: var(--font-weight-bold);
}
.icon-gap {
  vertical-align: -2px;
  margin-right: 4px;
}

/* ===== P5 学习路径视觉层级 ===== */
.path-hero-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.path-hero-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--font-weight-semibold);
}
.path-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-4);
  background: var(--color-bg-soft);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-4);
}
.summary-left {
  flex: 1;
  min-width: 0;
}
.summary-line {
  font-size: var(--font-size-body);
  color: var(--color-text-regular);
}
.summary-line .num {
  font-size: 22px;
  font-weight: var(--font-weight-bold);
  color: var(--color-primary);
  margin: 0 2px;
}
.summary-line .muted {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}
.summary-suggestion {
  margin-top: 6px;
  font-size: var(--font-size-label);
  color: var(--color-primary);
  font-weight: var(--font-weight-semibold);
}
.summary-suggestion.success {
  color: var(--color-success);
}
.summary-suggestion.muted {
  color: var(--color-text-secondary);
  font-weight: var(--font-weight-regular);
}
.summary-progress {
  width: 220px;
  flex-shrink: 0;
}
.path-chain-wrap {
  min-height: 120px;
}
.path-chain {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--space-2) 0;
}
.chain-node {
  display: flex;
  gap: var(--space-3);
  width: 100%;
  max-width: 560px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-text-muted);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.chain-node:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}
.chain-node.state-mastered {
  border-left-color: var(--color-success);
}
.chain-node.state-current {
  border-left-color: var(--color-primary);
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.15);
}
.chain-node.state-recommended {
  border-left-color: var(--color-warning);
}
.chain-node.state-unlearned {
  border-left-color: #dcdfe6;
  opacity: 0.72;
}
.chain-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: var(--font-weight-bold);
  flex-shrink: 0;
}
.state-mastered .chain-icon {
  background: var(--el-color-success-light-9);
  color: var(--color-success);
}
.state-current .chain-icon {
  background: var(--color-primary);
  color: var(--text-inverse);
}
.state-recommended .chain-icon {
  background: var(--el-color-warning-light-9);
  color: var(--color-warning);
}
.state-unlearned .chain-icon {
  background: var(--el-fill-color-light);
  color: var(--color-text-muted);
}
.chain-content {
  flex: 1;
  min-width: 0;
}
.chain-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.chain-name {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
}
.state-label {
  font-size: var(--font-size-caption);
  padding: 1px 8px;
  border-radius: var(--radius-lg);
  font-weight: var(--font-weight-semibold);
}
.state-label-mastered {
  color: var(--color-success);
  background: var(--el-color-success-light-9);
}
.state-label-current {
  color: var(--color-primary);
  background: var(--color-primary-light);
}
.state-label-recommended {
  color: var(--color-warning);
  background: var(--el-color-warning-light-9);
}
.state-label-unlearned {
  color: var(--color-text-secondary);
  background: var(--el-color-info-light-9);
}
.chain-reason {
  margin-top: 6px;
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
}
.chain-status {
  margin-top: 4px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
.chain-link {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  padding: var(--space-1) 0;
}
.path-actions {
  display: flex;
  justify-content: center;
  margin-top: var(--space-4);
}

/* ===== 收藏夹 ===== */
.fav-count {
  margin: var(--space-3) 0 var(--space-2);
  font-size: var(--font-size-body);
  color: var(--color-text-regular);
}
.fav-count .num {
  font-size: 18px;
  font-weight: var(--font-weight-bold);
  color: var(--color-primary);
  margin: 0 2px;
}
.fav-list {
  min-height: 220px;
}
.fav-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-4);
}
.fav-card {
  display: flex;
  flex-direction: column;
  padding: var(--space-4) var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-card);
  transition: box-shadow 0.15s, border-color 0.15s;
}
.fav-card:hover {
  border-color: var(--el-color-primary-light-8);
  box-shadow: var(--shadow-hover);
}
.fav-card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.fav-star {
  color: var(--color-warning);
  font-size: 16px;
  flex-shrink: 0;
}
.fav-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fav-desc {
  margin-top: var(--space-2);
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.fav-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.fav-empty-title {
  margin: 0 0 var(--space-1);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.fav-empty-sub {
  margin: 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.6;
}

/* 收藏按钮（QA 来源卡 / 学习路径） */
.source-fav {
  flex-shrink: 0;
  padding: 2px;
}
.chain-fav {
  flex-shrink: 0;
  margin-left: auto;
  padding: 2px;
}

/* ===== P11 学习总览（学习驾驶舱） ===== */
.ov-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.ov-hero-left {
  min-width: 0;
}
.ov-title {
  font-size: 18px;
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
}
.ov-sub {
  margin-top: var(--space-1);
  font-size: var(--font-size-label);
  color: var(--color-text-secondary);
}

/* 第一层：KPI 卡片 */
.ov-kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}
.ov-kpi {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
}
.ov-kpi-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.ov-kpi-meta {
  min-width: 0;
}
.ov-kpi-value {
  font-size: var(--font-size-number);
  font-weight: var(--font-weight-bold);
  line-height: 1.1;
  color: var(--color-text-primary);
  font-family: var(--font-family-number);
}
.ov-kpi-label {
  margin-top: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.ov-row {
  margin-bottom: var(--space-4);
}
.ov-card {
  height: 100%;
}
.ov-card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.ov-card-count {
  margin-left: auto;
  min-width: 22px;
  height: 22px;
  padding: 0 7px;
  border-radius: 11px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-bold);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* 第二层：掌握情况 */
.ov-state-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 180px;
}
.ov-state-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ov-state-glyph {
  width: 22px;
  text-align: center;
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  flex-shrink: 0;
}
.ov-state-name {
  width: 60px;
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
  flex-shrink: 0;
}
.ov-state-bar {
  flex: 1;
}
.ov-state-num {
  width: 32px;
  text-align: right;
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
  flex-shrink: 0;
}
.ov-state-glyph.st-mastered {
  color: var(--color-success);
}
.ov-state-glyph.st-current {
  color: var(--color-primary);
}
.ov-state-glyph.st-recommended {
  color: var(--color-warning);
}
.ov-state-glyph.st-unlearned {
  color: var(--color-text-muted);
}
.ov-state-total {
  margin-top: var(--space-1);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border-light);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

/* 继续学习 */
.ov-continue {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-height: 180px;
}
.ov-current {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-hover);
  border: 1px solid var(--el-color-primary-light-8);
  border-radius: var(--radius-lg);
}
.ov-current-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.ov-current-name {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
}
.ov-next-title {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}
.ov-next-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  margin-bottom: 6px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ov-next-item:hover {
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-5);
}
.ov-next-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-next-jump {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.ov-next-item:hover .ov-next-jump {
  color: var(--color-warning);
}
.ov-actions {
  display: flex;
  gap: 10px;
  margin-top: var(--space-3);
  flex-wrap: wrap;
}

/* 第三层：重点学习 + 收藏 */
.ov-focus-list,
.ov-fav-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.ov-focus-item {
  padding: 10px var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ov-focus-item:hover {
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
}
.ov-focus-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.ov-focus-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-focus-view {
  flex-shrink: 0;
  padding: 2px;
}
.ov-focus-reason {
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.ov-fav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ov-fav-item:hover {
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-5);
}
.ov-fav-star {
  color: var(--color-warning);
  flex-shrink: 0;
}
.ov-fav-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}


/* ===== Phase 7 统一学习上下文条 ===== */
.ctx-bar {
  margin-bottom: var(--space-4);
}
.ctx-bar :deep(.el-card__body) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.ctx-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
}
.ctx-label {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}
.ctx-name {
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-empty {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
}
.ctx-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

/* ===== 课程文档（在线阅读入口） ===== */
.sd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.sd-head-left {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
}
.sd-title {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.sd-course {
  font-size: var(--font-size-label);
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sd-head-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.sd-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.sd-item {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: 14px 4px;
  border-bottom: 1px solid var(--color-border-light);
}
.sd-item:last-child {
  border-bottom: none;
}
.sd-item-main {
  flex: 1;
  min-width: 0;
}
.sd-item-top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.sd-item-name {
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-medium);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sd-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 16px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--color-primary-light);
  color: var(--color-primary);
}
.sd-badge.is-progress {
  background: #fdf6ec;
  color: var(--color-warning);
}
.sd-badge.is-done {
  background: #f0f9eb;
  color: var(--color-success);
}
.sd-item-meta {
  margin-top: 3px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
.sd-item-bar {
  margin-top: 7px;
  height: 4px;
  max-width: 320px;
  border-radius: 2px;
  background: var(--color-border-light);
  overflow: hidden;
}
.sd-item-bar-fill {
  height: 100%;
  background: var(--color-primary);
  border-radius: 2px;
}
.sd-item-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .sd-item {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-3);
  }
  .sd-item-actions {
    justify-content: flex-end;
  }
}
.qa-side-course {
  font-size: var(--font-size-label);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-semibold);
  padding: 2px 0;
}

/* 响应式：KPI 卡片按断点换行（1200+ 四列 / 768-1199 两列 / <768 单列） */
@media (max-width: 1199px) {
  .ov-kpi-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 767px) {
  .ov-kpi-row {
    grid-template-columns: repeat(1, 1fr);
  }
}

/* ===== 做题练习（学生端题库） ===== */
.practice-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.practice-kpi {
  background: var(--bg-page, #f5f7fa);
  border-radius: 10px;
  padding: 12px 14px;
  text-align: center;
}
.practice-kpi-value {
  font-size: 22px;
  font-weight: 600;
}
.practice-kpi-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.chart-title-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 12px;
}
.table-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.cell-empty {
  color: #c0c4cc;
}
.practice-tip {
  font-size: 12px;
  color: #909399;
}
.practice-card {
  padding: 4px 2px;
}
.practice-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
/* 智能推荐（P2）：推荐理由与组卷说明 */
.practice-reco-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  color: #3a71c1;
  font-size: 12px;
}
.practice-reason {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  color: #b88230;
  font-size: 13px;
}
.practice-progress {
  color: #909399;
  font-size: 13px;
}
.practice-head-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}
.practice-stem {
  font-size: 16px;
  line-height: 1.7;
  font-weight: 600;
  margin-bottom: 14px;
  white-space: pre-wrap;
}
.practice-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
/* 选项整行可点，长选项自动换行（避免被 el-radio 默认高度裁切） */
.practice-option {
  display: flex;
  align-items: center;
  height: auto;
  padding: 8px 12px;
  margin-right: 0 !important;
  border: 1px solid var(--border-light, #ebeef5);
  border-radius: 8px;
  white-space: normal;
  line-height: 1.6;
}
.practice-option.is-checked {
  border-color: #4f6ef7;
  background: rgba(64, 158, 255, 0.06);
}
.practice-result {
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 8px;
  border-left: 4px solid #67c23a;
  background: rgba(103, 194, 58, 0.08);
}
.practice-result.bad {
  border-left-color: #f56c6c;
  background: rgba(245, 108, 108, 0.08);
}
/* 主观题待批改：中性色（既不是对也不是错） */
.practice-result.pending {
  border-left-color: #e6a23c;
  background: rgba(230, 162, 60, 0.08);
}
/* 填空题：多空输入 */
.practice-blanks {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}
.practice-blank-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.practice-blank-label {
  flex: 0 0 auto;
  min-width: 56px;
  color: var(--text-secondary);
  font-size: 13px;
}
.practice-result-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.practice-result-answer {
  margin-left: 8px;
  font-weight: 500;
  color: #606266;
}
.practice-analysis {
  margin-top: 8px;
  color: #606266;
  line-height: 1.7;
  white-space: pre-wrap;
}
.practice-actions {
  display: flex;
  gap: 10px;
  margin-top: 18px;
}
@media (max-width: 767px) {
  .practice-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .practice-head-actions {
    margin-left: 0;
  }
}

/* ============================================================
   v2 视觉增强（靛蓝科技体系）——同优先级后置覆盖
   ============================================================ */

/* ---- 统一上下文条：浅色渐变底 + 状态胶囊 ---- */
.ctx-bar :deep(.el-card__body) {
  background: linear-gradient(120deg, rgba(91,141,239,.07), rgba(139,92,246,.06));
  border-radius: var(--radius-lg);
  padding: 14px 18px;
}
.ctx-label {
  background: rgba(255,255,255,.7);
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border-light);
}
.ctx-name { color: var(--brand-700); }
.ctx-empty { font-weight: 500; }

/* ---- 学习总览 Hero ---- */
.ov-hero {
  position: relative;
  overflow: hidden;
}
.ov-hero::after {
  content: '';
  position: absolute;
  right: -40px;
  top: -60px;
  width: 200px;
  height: 200px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(91,141,239,.12), transparent 70%);
  pointer-events: none;
}
.ov-title { font-size: 19px; }

/* KPI 卡悬浮 + 图标圆角加强 */
.ov-kpi {
  transition: transform .25s ease, box-shadow .25s ease;
}
.ov-kpi:hover { transform: translateY(-3px); box-shadow: var(--shadow-hover); }
.ov-kpi-icon { border-radius: 13px; box-shadow: inset 0 1px 0 rgba(255,255,255,.5); }
.ov-kpi-value { font-size: 26px; }

.ov-card-title .el-icon { color: var(--brand-500); }
.ov-current {
  background: linear-gradient(120deg, rgba(91,141,239,.09), rgba(139,92,246,.07));
  border-color: var(--brand-200);
}
.ov-next-item:hover {
  background: var(--brand-50);
  border-color: var(--brand-200);
}
.ov-next-item:hover .ov-next-jump { color: var(--brand-500); }
.ov-focus-item:hover,
.ov-fav-item:hover {
  background: var(--brand-50);
  border-color: var(--brand-200);
}
.ov-fav-item:hover { background: #fff8ec; border-color: #f5d59a; }

/* ---- 智能问答：现代气泡 ---- */
.qa-side-course {
  background: var(--brand-50);
  color: var(--brand-700);
  border-radius: var(--radius-md);
  padding: 8px 10px !important;
  font-size: 13px;
}
.chat-box {
  background:
    radial-gradient(40% 30% at 100% 0, rgba(91,141,239,.05), transparent 70%),
    #f7f9fe;
  border-radius: var(--radius-lg);
  border-color: var(--border-light);
}
.chat-welcome { margin-top: 110px; }
.welcome-icon {
  width: 72px;
  height: 72px;
  margin: 0 auto 14px;
  border-radius: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  color: #fff !important;
  box-shadow: 0 12px 26px -10px rgba(79,110,247,.6);
}
.welcome-icon :deep(svg) { color: #fff; }
.msg-bubble {
  padding: 11px 15px;
  box-shadow: 0 2px 10px -4px rgba(22,34,66,.12);
  animation: kg-pop-in .25s ease both;
}
.msg-user .msg-bubble {
  background: var(--gradient-brand);
  border-bottom-right-radius: 4px;
}
.msg-ai .msg-bubble {
  background: #fff;
  border-color: var(--border-light);
  border-bottom-left-radius: 4px;
}
.source-card {
  background: #fff;
  border-radius: var(--radius-sm);
  transition: all .18s;
}
.source-card:hover { border-color: var(--brand-300); box-shadow: 0 4px 12px -6px rgba(79,110,247,.35); transform: translateX(2px); }
.kp-item { border-radius: var(--radius-sm); }
.kp-item:hover { border-color: var(--brand-300); background: var(--brand-50); }
.kp-item:hover .kp-jump { color: var(--brand-500); }

/* ---- 学习路径链：竖向时间轴质感 ---- */
.path-summary {
  background: linear-gradient(120deg, rgba(91,141,239,.07), rgba(34,192,138,.05));
  border-color: var(--brand-100);
}
.summary-line .num { color: var(--brand-600); }
.chain-node {
  border-left-width: 4px;
  border-radius: var(--radius-md);
  transition: transform .2s ease, box-shadow .2s ease;
}
.chain-node.state-current {
  background: linear-gradient(120deg, rgba(91,141,239,.1), rgba(139,92,246,.06));
  border-color: var(--brand-200);
  box-shadow: 0 8px 22px -12px rgba(79,110,247,.45);
}
.state-current .chain-icon {
  background: var(--gradient-brand) !important;
  color: #fff !important;
  box-shadow: 0 6px 14px -6px rgba(79,110,247,.7);
}
.state-mastered .chain-icon { box-shadow: inset 0 1px 0 rgba(255,255,255,.5); }
.state-recommended .chain-icon { box-shadow: inset 0 1px 0 rgba(255,255,255,.5); }
.chain-link {
  flex-direction: column;
  gap: 1px;
  padding: 2px 0;
  color: var(--brand-400);
}
.chain-link .el-icon { transform: rotate(180deg); }
.path-step {
  border-radius: var(--radius-sm);
  background: var(--brand-50);
  border-color: var(--brand-100);
}
.path-arrow { color: var(--brand-400); }
.rec-item {
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  border-bottom: none;
  background: var(--bg-soft);
  margin-bottom: 8px;
  transition: background .18s;
}
.rec-item:hover { background: var(--brand-50); }

/* ---- 收藏夹卡片 ---- */
.fav-card { border-radius: var(--radius-md); transition: transform .22s, box-shadow .22s, border-color .22s; }
.fav-card:hover { transform: translateY(-3px); border-color: var(--brand-200); }
.fav-actions { gap: 8px; }
.fav-actions .el-button {
  flex: 0 0 auto;
  padding-left: 12px;
  padding-right: 12px;
  margin-left: 0;
}

/* ---- 课程文档列表 ---- */
.sd-item {
  border-radius: var(--radius-md);
  padding: 14px 12px;
  border-bottom: 1px solid transparent;
  transition: background .18s;
}
.sd-item:hover { background: var(--brand-50); }
.sd-badge { border-radius: 999px; padding: 1px 9px; font-weight: 500; }
.sd-item-bar-fill { background: var(--gradient-brand); }

/* ---- 做题练习 ---- */
.practice-kpi {
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
  transition: transform .22s;
}
.practice-kpi:hover { transform: translateY(-2px); }
.practice-kpi-value { color: var(--brand-600); font-family: var(--font-family-number); }
.practice-option {
  border-radius: var(--radius-sm);
  transition: all .18s;
  cursor: pointer;
}
.practice-option:hover { border-color: var(--brand-300); background: var(--brand-50); }
.practice-option.is-checked {
  border-color: var(--brand-500);
  background: rgba(79,110,247,.07);
}
.practice-result {
  border-radius: var(--radius-sm);
  border-left-width: 4px;
  border-left-color: var(--success);
  background: rgba(24,184,122,.08);
}
.practice-result.bad {
  border-left-color: var(--danger);
  background: rgba(244,88,122,.08);
}

/* 总览欢迎面板（无学习上下文时内联选择课程/资料，减少空白） */
.welcome-card {
  max-width: 860px;
  margin: 24px auto;
}
.welcome-head {
  margin-bottom: 14px;
}
.welcome-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text-primary, #1c2438);
}
.welcome-sub {
  font-size: 12.5px;
  color: var(--color-text-secondary, #8590a8);
  margin-top: 4px;
  line-height: 1.6;
}
</style>
