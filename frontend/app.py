"""
Streamlit 前端：课程知识图谱智能构建与学习系统
启动方式: streamlit run frontend/app.py
"""
import streamlit as st
import requests
import json

# 配置页面
st.set_page_config(
    page_title="课程知识图谱系统",
    page_icon="📚",
    layout="wide"
)

# 后端API地址
API_BASE = "http://localhost:8000"

# 侧边栏：角色选择
st.sidebar.title("📚 课程知识图谱系统")
role = st.sidebar.radio("选择角色", ["👩‍🏫 教师端", "👨‍🎓 学生端"])

# =====================
# 教师端
# =====================
if role == "👩‍🏫 教师端":
    st.title("👩‍🏫 教师工作台")
    st.markdown("上传课程资料，自动生成知识图谱")

    tab1, tab2, tab3 = st.tabs(["📤 上传课程文档", "🔍 知识图谱预览", "✏️ 编辑图谱"])

    with tab1:
        st.subheader("上传课程资料")
        uploaded_file = st.file_uploader(
            "选择课程文档（支持 PDF / DOCX / TXT / MD）",
            type=["pdf", "docx", "txt", "md"]
        )
        course_name = st.text_input("课程名称（可选）")

        if uploaded_file:
            if st.button("🚀 开始分析并构建知识图谱", type="primary"):
                with st.spinner("正在处理..."):
                    # 发送文件到后端
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
                    try:
                        response = requests.post(
                            f"{API_BASE}/api/courses/upload",
                            files=files,
                            params={"course_name": course_name}
                        )
                        if response.status_code == 200:
                            result = response.json()
                            st.success(f"✅ 知识图谱构建完成！")
                            st.json(result)
                        else:
                            st.error(f"请求失败: {response.text}")
                    except Exception as e:
                        st.error(f"连接后端失败: {e}")
                        st.info("请确保后端服务已启动: `python -m uvicorn backend.app.main:app --reload`")

    with tab2:
        st.subheader("知识图谱可视化")
        st.info("使用 ECharts 渲染知识图谱（需连接后端和 Neo4j）")
        # TODO: 集成 ECharts 图可视化组件
        st.markdown("""
        ```html
        <!-- 此部分将使用 ECharts Graph 实现交互式可视化 -->
        <!-- 功能包括：节点拖拽、缩放、点击查看详情 -->
        ```
        """)

    with tab3:
        st.subheader("编辑知识图谱")
        st.info("教师可以手动编辑节点和关系（加�功能）")
        # TODO: 实现节点编辑、删除功能

# =====================
# 学生端
# =====================
else:
    st.title("👨‍🎓 学习空间")
    st.markdown("浏览课程知识图谱，智能学习辅助")

    tab1, tab2, tab3 = st.tabs(["🗺️ 知识图谱浏览", "💬 智能问答", "🎯 学习路径推荐"])

    with tab1:
        st.subheader("课程知识图谱")
        course_select = st.selectbox("选择课程", ["请选择课程..."])
        st.info("浏览知识点，点击节点查看详情。支持图谱缩放和拖拽。")

    with tab2:
        st.subheader("💬 智能问答")
        st.markdown("基于课程知识图谱的AI问答")

        question = st.text_input("输入你的问题", placeholder="例如：什么是机器学习？")

        if question and st.button("提问", type="primary"):
            with st.spinner("AI思考中..."):
                try:
                    response = requests.post(
                        f"{API_BASE}/api/qa/ask",
                        json={"question": question}
                    )
                    if response.status_code == 200:
                        result = response.json()
                        st.markdown(f"**回答：** {result['answer']}")
                        if result.get('sources'):
                            with st.expander("📖 参考来源"):
                                for src in result['sources']:
                                    st.markdown(f"- {src}")
                    else:
                        st.error("问答失败")
                except Exception as e:
                    st.error(f"连接后端失败: {e}")

    with tab3:
        st.subheader("🎯 个性化学习路径推荐")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**已掌握的知识点**")
            mastered_input = st.text_area(
                "输入你已掌握的知识点（每行一个）",
                placeholder="Python基础\n数据结构\n..."
            )
            mastered_list = [m.strip() for m in mastered_input.split('\n') if m.strip()]

            if st.button("🔍 推荐下一步学习内容"):
                st.info("推荐功能开发中...")

        with col2:
            st.markdown("**目标知识点**")
            target = st.text_input("输入你想学习的目标知识点")
            if st.button("🗺️ 生成学习路径"):
                st.info("路径生成功能开发中...")

# 底部状态栏
st.sidebar.divider()
try:
    response = requests.get(f"{API_BASE}/health", timeout=2)
    if response.status_code == 200:
        st.sidebar.success("🟢 后端服务在线")
    else:
        st.sidebar.warning("🟡 后端服务异常")
except Exception:
    st.sidebar.error("🔴 后端服务离线")
    st.sidebar.info("启动后端: `python -m uvicorn backend.app.main:app --reload`")
