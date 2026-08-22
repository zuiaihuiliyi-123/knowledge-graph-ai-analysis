# 基于AIGC的课程知识图谱智能构建与学习系统

## 项目概述

利用大语言模型（LLM）自动从课程资料（教材、PPT、教案等）中提取知识点和关系，构建可视化的知识图谱，并为学生提供个性化学习路径推荐和智能问答功能。

## 技术栈

- **后端**: Python FastAPI
- **前端**: Streamlit（原型）/ Vue
- **大模型API**: DeepSeek / 通义千问
- **图数据库**: Neo4j (AuraDB)
- **可视化**: ECharts Graph
- **文档解析**: PyPDF2, python-docx
- **RAG框架**: LangChain（可选）

## 项目结构

```
知识图谱AI分析/
├── backend/                # 后端代码
│   ├── app/
│   │   ├── api/           # API路由
│   │   │   ├── __init__.py
│   │   │   ├── auth.py          # 用户认证
│   │   │   ├── courses.py       # 课程管理
│   │   │   ├── knowledge_graph.py # 知识图谱CRUD
│   │   │   ├── qa.py            # 智能问答
│   │   │   └── learning_path.py # 学习路径推荐
│   │   ├── core/          # 核心配置
│   │   │   ├── __init__.py
│   │   │   ├── config.py        # 配置管理
│   │   │   └── database.py      # 数据库连接
│   │   ├── models/        # 数据模型
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── course.py
│   │   │   └── knowledge.py
│   │   ├── services/      # 业务逻辑
│   │   │   ├── __init__.py
│   │   │   ├── document_parser.py   # 文档解析
│   │   │   ├── knowledge_extractor.py # LLM知识提取
│   │   │   ├── kg_manager.py        # 知识图谱管理
│   │   │   ├── qa_service.py        # 智能问答(RAG)
│   │   │   └── path_recommender.py  # 学习路径推荐
│   │   ├── utils/         # 工具函数
│   │   │   ├── __init__.py
│   │   │   └── text_processor.py    # 文本预处理
│   │   ├── __init__.py
│   │   └── main.py        # 应用入口
│   └── requirements.txt   # 后端依赖
├── frontend/              # 前端代码
│   └── app.py             # Streamlit应用
├── data/                  # 数据目录
│   └── sample_docs/       # 示例课程文档
├── docs/                  # 项目文档
├── tests/                 # 测试代码
└── README.md
```

## 快速开始

```bash
# 1. 安装依赖
cd backend
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key 等信息

# 3. 启动后端
python -m uvicorn app.main:app --reload

# 4. 启动前端
cd ../frontend
streamlit run app.py
```

## 功能模块

1. **文档上传与解析** — 支持 PDF/DOCX/TXT/Markdown
2. **LLM知识提取** — 自动识别实体和关系（准确率≥70%）
3. **知识图谱可视化** — ECharts交互式展示，支持缩放/拖拽
4. **智能问答(RAG)** — 基于课程内容的精准回答（响应≤15秒）
5. **学习路径推荐** — 基于知识图谱的个性化推荐
6. **双用户界面** — 教师端（管理）+ 学生端（学习）

后端用pycharm的python语言开发，前端用vscode开发，用Vue，前端的界面可以参考其他的网页，直接让ai辅助设计，将所有的文件的代码可以放到sealos中部署运行，这样可以直接在网页上访问，python前后端的连接不是特别了解，之前学习和看的都是使用Node.js进行开发的，先试着使用python进行开发试试，不会的就问ai，最后如果实在是不行的话就结合Node.js进行开发软件，尽量在15号之前把所有的软件内容大纲都完成，最后就只剩细节的调整还有展示的部分。每天都要抽出一点时间来管理一下这个项目，推动一下。还需要设计一个ai的接口，给ai设计skill用来构建知识图谱，知识图谱的构建又是一个问题，并且还有页面系统的功能需要设计，还有解析PDF文档的（或者其他文档的）内容，需要对于整个项目有清晰的把握，然后将任务布置下去。
