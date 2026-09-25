"""
演示数据填充脚本（P0 冷启动）：为指定课程批量生成「结构型题目」+ 学生作答记录。

为什么需要这个脚本（实测 backend/data/app.db）：
- t_question 只有 6 道题（全部在课程 63），t_answer_record 为 0 条，
  课程 63 只有 1 名学生会做题 —— 掌握度指标与题目推荐算法没有数据可算，属于空跑。
- 本脚本先补齐数据，让后续的「掌握度 / 弱点推荐 / 遗忘曲线」有真实输入。

题目怎么造（不依赖 LLM，答案由构造保证正确）：
  全部基于 Neo4j 图谱已有事实（name / description / category）生成，五类模板：
    1. 定义匹配（SINGLE）：给出 description，问它是哪个知识点   → 答案 = 该点名称
    2. 描述归属（JUDGE） ：陈述该点的描述是否正确（错误项张冠李戴）→ 真假由 description 决定
    3. 描述配对（MULTI） ：4 组「知识点：描述」选出配对正确的两组 → 两对两错，答案唯一
    4. 描述选择（SINGLE）：四个描述里哪个属于该知识点            → 答案 = 该点 description
    5. 类别判断（SINGLE）：该知识点属于哪个类别                  → 答案 = 该点 category
  前 4 类只依赖 name/description，因此在「类别全是概念」这类图谱上依然成立；
  第 5 类在图谱类别单一时区分度有限，故排在最后作为兜底。
  这样做的好处：**每道题的答案都可被图谱事实验证**，不会出现「造出来的题本身是错的」
  （LLM 生成题虽更自然，但需要人工审核，见 docs/题库功能说明.md 第八节，属后续 R4 工作）。

作答怎么造：
  - 每个学生的「能力值」决定答对概率，题目难度参与修正，后续作答有学习增益（越做越对）；
  - 作答时间回溯散布在最近 N 天内（多次作答逐次推进），以便展示遗忘曲线 / 时间衰减；
  - 判分一律调用线上同一个 grade()，因此库里的 is_correct 与线上判分口径永远一致。

安全边界（只动自己造的数据）：
  - 题目一律 source='IMPORT'，重跑时只清理本课程 + source='IMPORT' 的行；
  - 作答记录只清理由这些 IMPORT 题产生的行；
  - 学习标记一律 source='SYSTEM'（与教师/学生手动产生的 MANUAL 区分）；
  - 教师手工建的题（source='MANUAL'）永不触碰。

运行方式（在 backend 目录下）：
    python seed_practice_data.py                      # 默认课程 63，先清理旧演示数据再重建
    python seed_practice_data.py --course-id 63 --per-kp 3 --students 5
    python seed_practice_data.py --dry-run            # 只打印计划，不写库
    python seed_practice_data.py --keep               # 追加而不清理上一轮 IMPORT 数据
    python seed_practice_data.py --clean-only         # 只清理本脚本写入的数据（撤销演示数据用）
    python seed_practice_data.py --offline-kp         # 强制用 SQLite 已有 kp_id（Neo4j 未启动时）
    python seed_practice_data.py --kp-json backup/neo4j_20260905_145113.json
        # Neo4j 未启动时的推荐做法：直接读图谱导出 JSON 里的 name/description/category 造题
        # （导出 JSON 里的 kp_id 必须仍是当前图谱中的有效 id，否则会造出悬空知识点的题）
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.security import hash_password
from app.core.sql_database import sql_db
from app.services.question_service import grade

# ---------- 默认参数 ----------

DEFAULT_COURSE_ID = 63
DEFAULT_PER_KP = 3            # 每个知识点生成的题目数（模板上限 5 个）
DEFAULT_MAX_KP = 40           # 参与造题的知识点上限（控制数据规模）
DEFAULT_STUDENTS = 5          # 课程内至少保留的演示学生数
DEFAULT_DAYS = 21             # 作答时间回溯跨度（天）
DEFAULT_COVERAGE = 0.75       # 学生对题目的作答覆盖率（留下"没做过"的题，更接近真实）

DEMO_PASSWORD = "demo1234"    # 新建演示学生的初始密码（仅演示环境使用）
DEMO_USERNAME_PREFIX = "demo_stu"

# 演示学生能力值池（按顺序分配：强 / 中 / 偏弱 / 中上 / 弱），保证"学情差异"肉眼可见
ABILITY_POOL = (0.88, 0.62, 0.45, 0.72, 0.30)

# 类别 → 基础难度（图谱里没有难度信息，按知识点类别给出可解释的默认值）
BASE_DIFFICULTY = {"概念": 1, "定理": 2, "公式": 3, "方法": 4}
VALID_CATEGORIES = ("概念", "定理", "公式", "方法")


# ---------- 通用小工具 ----------

def _now() -> datetime:
    return datetime.now()


def _fmt(dt: datetime) -> str:
    """统一时间格式，与 sql_database._now() 一致（MySQL DATETIME 兼容）"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _load_json(value, default):
    """回读库内 JSON 文本；解析失败返回 default（对齐 question_service._loads 的语义）"""
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _norm_key(value) -> str:
    """选项键归一化（脚本内自造的键都是 A/B/C/D，仅需大小写与空白归一）"""
    return str(value or "").strip().upper()


def _truncate(text, limit=80) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _pick_texts(rng: random.Random, peers: list, kp: dict, n: int = 3) -> list:
    """从同文档其他知识点中挑 n 个名称作为干扰项（用真实术语，避免出现臆造的假名词）"""
    pool = [p["name"] for p in peers if p["name"] and p["name"] != kp["name"]]
    rng.shuffle(pool)
    return pool[:n]


def _shuffle_options(rng: random.Random, correct_texts: list, distractor_texts: list) -> tuple:
    """混排选项并计算答案键，返回 (options, answer_keys)。"""
    items = [{"text": t, "ok": True} for t in correct_texts] + \
            [{"text": t, "ok": False} for t in distractor_texts]
    rng.shuffle(items)
    options, answer = [], []
    for i, item in enumerate(items):
        key = chr(65 + i)          # A / B / C / D …
        options.append({"key": key, "text": item["text"]})
        if item["ok"]:
            answer.append(key)
    return options, answer


# ---------- 定位课程 / 文档 / 知识点 ----------

def resolve_course(course_id: int) -> dict:
    course = sql_db.get_course(course_id)
    if course is None:
        print(f"✗ 课程不存在：course_id={course_id}")
        print("  可用的课程：")
        for c in sql_db.list_courses():
            print(f"    {c['course_id']:>3}  {c['course_name']}")
        sys.exit(2)
    return course


def resolve_document(course_id: int, document_id) -> dict:
    """定位文档：显式传入则校验归属；否则取该课程下（优先）抽取完成的第一个文档。

    图谱按 course_id + document_id 隔离，所以必须落到具体文档，不能只到课程级。
    """
    if document_id is not None:
        doc = sql_db.get_document(document_id)
        if doc is None:
            print(f"✗ 文档不存在：document_id={document_id}")
            sys.exit(2)
        if doc["course_id"] != course_id:
            print(f"✗ 文档 {document_id} 不属于课程 {course_id}")
            sys.exit(2)
        return doc

    docs = sql_db.list_documents_by_course(course_id)
    if not docs:
        print(f"✗ 课程 {course_id} 下没有任何文档，无法定位知识点作用域")
        sys.exit(2)
    completed = [d for d in docs if d.get("extract_status") == "COMPLETED"]
    return (completed or docs)[0]


def load_knowledge_points_from_json(path: str, course_id: int, document_id) -> list:
    """从图谱导出 JSON 读取知识点（Neo4j 不可用时的离线造题数据源）。

    兼容两种格式：
      1) backend/backup/neo4j_*.json 的导出格式：{"nodes": [{"props": {...}}, ...]}
      2) 简化列表格式：[{"kp_id","name","category","description"}, ...]
    过滤规则：带 course_id 的节点必须等于当前课程；带 document_id 的必须等于当前文档
    （导出文件中缺这两个字段的节点视为匹配，以兼容文档作用域改造前的旧备份）。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    nodes = raw.get("nodes") if isinstance(raw, dict) else raw

    kps, skipped = [], 0
    for node in nodes or []:
        props = node.get("props", node) if isinstance(node, dict) else {}
        if props.get("course_id") not in (None, course_id):
            continue
        if props.get("document_id") not in (None, document_id):
            continue
        kp_id = props.get("kp_id") or props.get("id")
        if not kp_id or str(kp_id).startswith("4:"):
            skipped += 1
            continue                       # 只认业务 kp_id，不认 Neo4j 内部 elementId
        kps.append({
            "kp_id": kp_id,
            "name": props.get("name") or kp_id,
            "category": props.get("category") or "概念",
            "description": props.get("description") or "",
        })
    if skipped:
        print(f"    ⚠ 跳过 {skipped} 个没有业务 kp_id 的节点")
    return kps


def fetch_knowledge_points(course_id: int, document_id, limit: int, offline: bool,
                           kp_json: str = None) -> list:
    """取该课程 + 该文档下的知识点清单（kp_id / name / category / description）。

    优先级：--kp-json 指定的导出文件 > Neo4j 实时查询 > SQLite 已有 kp_id 兜底。
    最后一种兜底只有 kp_id、没有名称与描述，只能出「类别判断」类题目（仅用于验证流程）。
    """
    if kp_json:
        kps = load_knowledge_points_from_json(kp_json, course_id, document_id)
        print(f"  知识点来源：导出文件 {kp_json}（{len(kps)} 个）")
        return kps[:limit]

    if not offline:
        try:
            rows = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
                "RETURN n.kp_id AS kp_id, n.name AS name, n.category AS category, "
                "       n.description AS description "
                "ORDER BY n.kp_id "
                "LIMIT $limit",
                {"cid": course_id, "did": document_id, "limit": limit},
            )
            kps = [
                {
                    "kp_id": r.get("kp_id") or r.get("name"),
                    "name": r.get("name") or r.get("kp_id"),
                    "category": r.get("category") or "概念",
                    "description": r.get("description") or "",
                }
                for r in rows
                if r.get("kp_id")
            ]
            if kps:
                print(f"  知识点来源：Neo4j（course_id={course_id}, document_id={document_id}）")
                return kps
            print("  ⚠ Neo4j 中该文档没有知识点，退化为 SQLite 已有 kp_id")
        except Exception as e:
            print(f"  ⚠ Neo4j 不可用（{type(e).__name__}），退化为 SQLite 已有 kp_id")

    known, seen = [], set()
    # L2：知识点关联以 t_question_kp 为准——一题多挂时 t_question.kp_id 只有主知识点，
    # 直接读它会把「仅作为次要知识点」的 kp 漏掉。
    for r in sql_db._query(
        "SELECT DISTINCT qk.kp_id AS kp_id FROM t_question_kp qk "
        "JOIN t_question q ON q.question_id = qk.question_id "
        "WHERE q.course_id = ?",
        (course_id,),
    ):
        if r["kp_id"] not in seen:
            seen.add(r["kp_id"])
            known.append({"kp_id": r["kp_id"], "name": r["kp_id"], "category": "概念",
                          "description": "", "offline": True})
    for r in sql_db._query(
        "SELECT kp_id FROM t_learning_record WHERE course_id = ?", (course_id,),
    ):
        if r["kp_id"] not in seen:
            seen.add(r["kp_id"])
            known.append({"kp_id": r["kp_id"], "name": r["kp_id"], "category": "概念",
                          "description": "", "offline": True})
    return sorted(known, key=lambda x: x["kp_id"])[:limit]


# ---------- 清理上一轮演示数据 ----------

def clean_previous_seed(course_id: int) -> dict:
    """清理上一轮由本脚本写入的数据（只认 source 标记，绝不碰教师手工数据）"""
    question_ids = [
        r["question_id"] for r in sql_db._query(
            "SELECT question_id FROM t_question WHERE course_id = ? AND source = 'IMPORT'",
            (course_id,),
        )
    ]
    removed = {"questions": 0, "answers": 0, "favorites": 0, "learning": 0}
    conn = sql_db._connect()
    try:
        if question_ids:
            placeholders = ",".join("?" * len(question_ids))
            cur = conn.execute(
                f"DELETE FROM t_answer_record WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            )
            removed["answers"] = cur.rowcount
            cur = conn.execute(
                f"DELETE FROM t_question_favorite WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            )
            removed["favorites"] = cur.rowcount
            # L2：知识点关联（t_question_kp）有物理外键指向 t_question 且连接已开 foreign_keys=ON，
            # 必须先删关联行，否则下面的删题语句直接抛 FOREIGN KEY constraint failed。
            conn.execute(
                f"DELETE FROM t_question_kp WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            )
            cur = conn.execute(
                f"DELETE FROM t_question WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            )
            removed["questions"] = cur.rowcount
        # 学习标记只清 SYSTEM（脚本造的那批）；MANUAL 是真实用户行为，必须保留
        cur = conn.execute(
            "DELETE FROM t_learning_record WHERE course_id = ? AND source = 'SYSTEM'",
            (course_id,),
        )
        removed["learning"] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return removed


# ---------- 演示学生 ----------

def ensure_students(course_id: int, want: int, dry_run: bool) -> list:
    """确保课程内至少有 want 名演示学生（不足则新建 demo_stuNN 并以 approved 入课）"""
    students = []
    for uid in sorted(sql_db.list_member_user_ids(course_id, "approved", role="student")):
        user = sql_db.get_user_by_id(uid) or {}
        students.append({"user_id": uid, "username": user.get("username") or str(uid),
                         "created": False})

    missing = max(0, want - len(students))
    print(f"  课程现有学生 {len(students)} 名，需要新建 {missing} 名")
    for n in range(missing):
        username = f"{DEMO_USERNAME_PREFIX}{n + 1:02d}"
        existing = sql_db.get_user_by_username(username)
        if existing:
            uid = existing["user_id"]
        elif dry_run:
            print(f"    [dry-run] 将创建学生 {username}（密码 {DEMO_PASSWORD}）并入课")
            students.append({"user_id": -1, "username": username, "created": True})
            continue
        else:
            uid = sql_db.create_user(
                username, hash_password(DEMO_PASSWORD), role="student",
                display_name=f"演示学生{n + 1:02d}",
            )
            print(f"    新建学生 {username}（user_id={uid}，密码 {DEMO_PASSWORD}）")

        if not dry_run:
            # 直接以「已通过」身份入课：join_source=import 与"教师导入名单"同源语义
            sql_db.upsert_membership(course_id, uid, role="student", status="approved",
                                     join_source="import")
        if all(s["user_id"] != uid for s in students):
            students.append({"user_id": uid, "username": username, "created": True})

    # 能力值按序分配：保证同时存在强学生与弱学生，学情差异才看得出来
    for i, s in enumerate(students):
        s["ability"] = ABILITY_POOL[i % len(ABILITY_POOL)]
    return students


# ---------- 题目生成（四类模板，答案由图谱事实保证正确） ----------

def _q_definition_match(rng, kp, peers, by_category):
    """模板 1：单选·定义匹配 —— 给出描述，问是哪个知识点"""
    desc = _truncate(kp.get("description"), 80)
    if not desc or desc == kp.get("name"):
        return None                       # 无描述无法出题（离线兜底模式下必然如此）
    distractors = _pick_texts(rng, peers, kp, 3)
    if len(distractors) < 3:
        return None                       # 干扰项不足（同文档知识点太少）
    options, answer = _shuffle_options(rng, [kp["name"]], distractors)
    return {
        "q_type": "SINGLE",
        "stem": f"下列知识点中，哪一项对应下面的描述？\n「{desc}」",
        "options": options,
        "answer": answer[0],
        "analysis": f"「{kp['name']}」：{desc}",
    }


def _q_desc_judge(rng, kp, peers, by_category):
    """模板 2：判断·描述归属 —— 陈述该点的描述是否正确（错误陈述张冠李戴）

    比"类别判断"更有区分度：不依赖图谱类别的多样性（实测部分课程类别全是「概念」）。
    """
    desc = _truncate(kp.get("description"), 60)
    if not desc:
        return None
    others = [p for p in peers
              if p["kp_id"] != kp["kp_id"] and (p.get("description") or "").strip()]
    is_true = (not others) or rng.random() < 0.5
    stated = desc if is_true else _truncate(rng.choice(others)["description"], 60)
    return {
        "q_type": "JUDGE",
        "stem": f"知识点「{kp['name']}」的描述是：{stated}",
        "options": [],
        "answer": "true" if is_true else "false",
        "analysis": f"知识点「{kp['name']}」的正确描述：{desc}",
    }


def _q_desc_multi(rng, kp, peers, by_category):
    """模板 3：多选·描述配对 —— 4 组「知识点：描述」中选出配对正确的两组

    构造方式：正确项 = (A, A的描述)、(B, B的描述)；错误项 = (C, D的描述)、(D, C的描述)，
    因此答案由图谱事实唯一确定，且**不依赖类别多样性**。
    """
    if not (kp.get("description") or "").strip():
        return None
    rest = [p for p in peers
            if p["kp_id"] != kp["kp_id"] and (p.get("description") or "").strip()]
    if len(rest) < 3:
        return None                      # 描述不足，无法组出「两对两错」的多选
    rng.shuffle(rest)
    a, b, c, d = kp, rest[0], rest[1], rest[2]
    options, answer = _shuffle_options(
        rng,
        [f"「{a['name']}」：{_truncate(a['description'], 50)}",
         f"「{b['name']}」：{_truncate(b['description'], 50)}"],
        [f"「{c['name']}」：{_truncate(d['description'], 50)}",
         f"「{d['name']}」：{_truncate(c['description'], 50)}"],
    )
    return {
        "q_type": "MULTI",
        "stem": "下列「知识点：描述」配对中，哪些是正确的？（多选）",
        "options": options,
        "answer": sorted(answer),
        "analysis": (f"正确配对为：{a['name']} → {_truncate(a['description'], 50)}；"
                     f"{b['name']} → {_truncate(b['description'], 50)}。"
                     f"另外两组把知识点与别人的描述错配了。"),
    }


def _q_name_to_desc_single(rng, kp, peers, by_category):
    """模板 4：单选·描述选择 —— 四个描述里哪个属于该知识点（模板 1 的反向问法）"""
    desc = _truncate(kp.get("description"), 60)
    if not desc:
        return None
    others = [_truncate(p.get("description"), 60) for p in peers
              if p["kp_id"] != kp["kp_id"] and (p.get("description") or "").strip()]
    rng.shuffle(others)
    if len(others) < 3:
        return None
    options, answer = _shuffle_options(rng, [desc], others[:3])
    return {
        "q_type": "SINGLE",
        "stem": f"下列哪一项是知识点「{kp['name']}」的描述？",
        "options": options,
        "answer": answer[0],
        "analysis": f"「{kp['name']}」：{desc}",
    }


def _q_category_single(rng, kp, peers, by_category):
    """模板 5：单选·类别判定 —— 该知识点属于哪个类别（兜底：图谱类别单一时区分度有限）"""
    category = kp.get("category")
    if category not in VALID_CATEGORIES:
        return None
    others = [c for c in VALID_CATEGORIES if c != category][:3]
    options, answer = _shuffle_options(rng, [category], others)
    # 离线兜底模式下 category 是脚本默认值（未经图谱校验），必须在解析里注明，避免被当成真题
    note = ("（占位题：离线兜底模式生成，类别取脚本默认值、未与图谱校验；"
            "图谱恢复后重跑 seed_practice_data.py 即可自动替换为真题）"
            if kp.get("offline") else "")
    return {
        "q_type": "SINGLE",
        "stem": f"知识点「{kp['name']}」在本课程图谱中属于哪一类别？",
        "options": options,
        "answer": answer[0],
        "analysis": f"图谱中「{kp['name']}」的 category 为「{category}」。{note}",
    }


# 模板轮转顺序：保证每个知识点都能拿到 单选 / 判断 / 多选 三种题型
QUESTION_BUILDERS = (
    _q_definition_match, _q_desc_judge, _q_desc_multi,
    _q_name_to_desc_single, _q_category_single,
)


def build_questions(kps: list, per_kp: int, rng: random.Random) -> list:
    """为每个知识点生成 per_kp 道题（模板轮转；无法出题的模板自动跳过）"""
    by_category = {}
    for kp in kps:
        by_category.setdefault(kp.get("category"), []).append(kp)

    planned, skipped = [], 0
    for kp in kps:
        made, guard = 0, 0
        while made < per_kp and guard < len(QUESTION_BUILDERS) * 2:
            builder = QUESTION_BUILDERS[guard % len(QUESTION_BUILDERS)]
            guard += 1
            question = builder(rng, kp, kps, by_category)
            if question is None:
                continue
            # 难度：按类别给基础值，同一知识点的多道题依次递增（1-5 全覆盖，便于难度适配实验）
            base = BASE_DIFFICULTY.get(kp.get("category"), 3)
            question["difficulty"] = min(5, max(1, base + made))
            question["kp_id"] = kp["kp_id"]
            planned.append(question)
            made += 1
        if made == 0:
            skipped += 1
    if skipped:
        print(f"  ⚠ {skipped} 个知识点无法出题（缺 description 或同文档知识点太少）")
    return planned


def insert_questions(course_id: int, document_id, creator_id: int, planned: list) -> list:
    """写入题目（source='IMPORT'，用于与教师手工题区分、支持重跑清理）"""
    question_ids = [
        sql_db.create_question(
            course_id=course_id, document_id=document_id, kp_id=q["kp_id"],
            q_type=q["q_type"], stem=q["stem"], options=q["options"], answer=q["answer"],
            analysis=q["analysis"], difficulty=q["difficulty"],
            created_by=creator_id, source="IMPORT",
        )
        for q in planned
    ]
    return question_ids


# ---------- 作答生成 ----------

def _load_options(row) -> list:
    """回读 options 为 [{"key","text"}]（兼容纯字符串写法）"""
    raw = _load_json(row.get("options"), [])
    options = []
    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                options.append({"key": _norm_key(item.get("key") or chr(65 + i)),
                                "text": str(item.get("text") or "")})
            else:
                options.append({"key": chr(65 + i), "text": str(item)})
    return options


def _candidate_answers(row, rng) -> list:
    """作答候选值：第 1 个是正确答案，其余是错误答案（顺序不假设，最终一律用 grade() 复核）"""
    q_type = row["q_type"]
    if q_type == "JUDGE":
        return ["true", "false"]
    keys = [o["key"] for o in _load_options(row)] or ["A", "B", "C", "D"]
    answer = _load_json(row.get("answer"), None)
    if q_type == "MULTI":
        right = [_norm_key(k) for k in (answer if isinstance(answer, list) else [answer]) if k]
        candidates = [list(right)]
        if len(right) > 1:
            candidates.append(list(right[:-1]))      # 少选 → 判错
        candidates.extend(list(right) + [k] for k in keys if k not in right)   # 错选/多选 → 判错
        return candidates
    correct = _norm_key(answer)
    return [answer] + [k for k in keys if k != correct]


def _pick_answer(row, want_correct: bool, rng: random.Random) -> tuple:
    """构造一个作答值，返回 (作答值, 实际是否正确, 是否命中期望)。

    一律用线上同一个 grade() 复核，因此库里的 is_correct 与判分口径永远一致
    （不会出现"库里写着答对、判分却读成答错"的脏数据）。
    """
    candidates = _candidate_answers(row, rng)
    ordered = candidates if want_correct else list(reversed(candidates))
    for value in ordered:
        if grade(row, value)["is_correct"] == want_correct:
            return value, want_correct, True
    value = candidates[0] if candidates else None
    return value, grade(row, value)["is_correct"], False


def seed_answers(course_id: int, students: list, rows: list, days: int,
                 coverage: float, rng: random.Random) -> dict:
    """模拟一段时间跨度的作答历史（含多次作答与学习增益）。

    为什么这里用原生 SQL 而不是 sql_db.add_answer_record：后者把 answered_at 固定写成
    now()，而演示数据需要"过去 N 天"的时间分布，否则遗忘曲线 / 时间衰减这类信号无从体现。
    写入时仍与 DAO 保持一致：user_answer 走 json.dumps（等价于 _json_text），
    score 用 100/0（等价于 grade()）。
    """
    now = _now()
    total, matched = 0, 0
    per_student, kp_stats = {}, {}
    conn = sql_db._connect()
    try:
        for student in students:
            ability = student["ability"]
            answered, correct = 0, 0
            for row in rows:
                if rng.random() > coverage:
                    continue                    # 刻意留白：并非每个学生都做过每道题
                attempts = 1 + int(rng.random() < 0.45) + int(rng.random() < 0.20)
                first_days_ago = rng.uniform(1, days)
                for a in range(attempts):
                    # 答对概率 = 能力值 - 难度修正 + 学习增益（同一题越往后越可能答对）
                    p = ability - 0.10 * ((row.get("difficulty") or 3) - 3) + 0.15 * a
                    want_correct = rng.random() < min(0.98, max(0.05, p))
                    value, is_correct, hit = _pick_answer(row, want_correct, rng)
                    matched += int(hit)
                    days_ago = max(0.0, first_days_ago - a * rng.uniform(0.5, 2.0))
                    answered_at = _fmt(now - timedelta(days=days_ago))
                    conn.execute(
                        "INSERT INTO t_answer_record "
                        "(user_id, course_id, document_id, question_id, user_answer, "
                        " is_correct, score, grade_source, answered_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (student["user_id"], course_id, row.get("document_id"),
                         row["question_id"], json.dumps(value, ensure_ascii=False),
                         1 if is_correct else 0, 100.0 if is_correct else 0.0,
                         "AUTO", answered_at),
                    )
                    total += 1
                    answered += 1
                    correct += int(is_correct)
                    kp_id = row.get("kp_id")
                    if kp_id:
                        st = kp_stats.setdefault((student["user_id"], kp_id),
                                                 {"attempts": 0, "correct": 0, "last_at": None})
                        st["attempts"] += 1
                        st["correct"] += int(is_correct)
                        if st["last_at"] is None or answered_at > st["last_at"]:
                            st["last_at"] = answered_at
            per_student[student["username"]] = {
                "answered": answered, "correct": correct,
                "rate": round(correct / answered * 100, 1) if answered else 0.0,
            }
        conn.commit()
    finally:
        conn.close()
    return {"answers": total, "matched": matched,
            "per_student": per_student, "kp_stats": kp_stats}


def seed_learning(course_id: int, document_id, kp_stats: dict) -> dict:
    """按作答表现回填学习标记（source='SYSTEM'，与人工 MANUAL 区分）

    规则：作答≥2 次且正确率≥75% → MASTERED；正确率<50% → LEARNING；
    其余（半懂）刻意不写，让"掌握度"有中间态可算。

    ⚠ 保护既有 MANUAL 记录：t_learning_record 的 UNIQUE(user_id, course_id, kp_id) 意味着
    upsert 会**覆盖**同一 (用户, 知识点) 上学生自己标记过的记录（连 source 一起改成 SYSTEM，
    之后重跑时的清理又会把它删掉 → 真实数据丢失）。因此这里先取出该课程的 MANUAL 键集合，
    凡是学生手动标记过的知识点一律跳过，绝不覆盖。
    """
    protected = {(r["user_id"], r["kp_id"]) for r in sql_db._query(
        "SELECT user_id, kp_id FROM t_learning_record "
        "WHERE course_id = ? AND source = 'MANUAL'", (course_id,))}

    created = {"MASTERED": 0, "LEARNING": 0}
    skipped = 0
    for (user_id, kp_id), st in kp_stats.items():
        if st["attempts"] == 0:
            continue
        if (user_id, kp_id) in protected:
            skipped += 1
            continue                        # 学生手动标记过：保留原记录
        rate = st["correct"] / st["attempts"]
        if st["attempts"] >= 2 and rate >= 0.75:
            status = "MASTERED"
        elif rate < 0.5:
            status = "LEARNING"
        else:
            continue
        sql_db.upsert_learning_record(
            user_id=user_id, course_id=course_id, document_id=document_id, kp_id=kp_id,
            status=status, mastery_level=int(round(rate * 100)), source="SYSTEM",
            last_learned_at=st["last_at"],
        )
        created[status] += 1
    created["skipped_manual"] = skipped
    return created


def verify_seed(course_id: int) -> dict:
    """自洽性校验：把库里每条作答重新喂给线上判分函数，比对落库的 is_correct。

    目的：证明"造出来的数据经得起判分口径检验"——若出现不一致，说明题目答案或作答值
    有构造错误，必须修脚本而不是改数据。
    """
    _, rows = sql_db.list_questions(course_id, page=1, page_size=100000)
    qmap = {r["question_id"]: r for r in rows}
    answers = sql_db.list_answer_records_by_course(course_id)
    mismatch, checked = 0, 0
    for a in answers:
        question = qmap.get(a["question_id"])
        if question is None:
            continue                      # 教师手工题可能不属于本课程查询范围
        value = _load_json(a["user_answer"], None)
        checked += 1
        if grade(question, value)["is_correct"] != bool(a["is_correct"]):
            mismatch += 1
    return {"checked": checked, "mismatch": mismatch, "total": len(answers)}


def print_overview(course_id: int):
    """打印课程数据概览 + 判分自洽性校验（正常生成与 --clean-only 两种模式复用）"""
    print("[9] 完成，当前课程数据概览")
    total_q = sql_db.list_questions(course_id, page=1, page_size=1)[0]
    import_count = sql_db._query_one(
        "SELECT count(*) AS cnt FROM t_question WHERE course_id = ? AND source = 'IMPORT'",
        (course_id,),
    )["cnt"]
    answer_count = sql_db._query_one(
        "SELECT count(*) AS cnt FROM t_answer_record WHERE course_id = ?", (course_id,),
    )["cnt"]
    kp_with_q = sql_db._query_one(
        "SELECT count(DISTINCT qk.kp_id) AS cnt FROM t_question_kp qk "
        "JOIN t_question q ON q.question_id = qk.question_id "
        "WHERE q.course_id = ?",
        (course_id,),
    )["cnt"]
    covered_kp = sql_db._query_one(
        "SELECT count(DISTINCT qk.kp_id) AS cnt FROM t_answer_record a "
        "JOIN t_question_kp qk ON qk.question_id = a.question_id "
        "WHERE a.course_id = ?",
        (course_id,),
    )["cnt"]
    manual_lr = sql_db._query_one(
        "SELECT count(*) AS cnt FROM t_learning_record "
        "WHERE course_id = ? AND source = 'MANUAL'", (course_id,),
    )["cnt"]
    system_lr = sql_db._query_one(
        "SELECT count(*) AS cnt FROM t_learning_record "
        "WHERE course_id = ? AND source = 'SYSTEM'", (course_id,),
    )["cnt"]
    print(f"    题目总数 {total_q}（其中演示题 {import_count}）")
    print(f"    有题知识点 {kp_with_q} 个 / 已有作答的知识点 {covered_kp} 个")
    print(f"    作答记录 {answer_count} 条")
    print(f"    学习标记 MANUAL {manual_lr} 条（用户自评，永久保留）/ SYSTEM {system_lr} 条（脚本生成）")

    print("[10] 自洽性校验（重新用线上判分函数复核每条作答）")
    verified = verify_seed(course_id)
    if verified["checked"] == 0:
        print("    无作答记录，跳过")
    elif verified["mismatch"] == 0:
        print(f"    ✓ {verified['checked']} 条作答与判分口径完全一致")
    else:
        print(f"    ✗ {verified['mismatch']}/{verified['checked']} 条作答与判分口径不一致，请检查题目答案")


# ---------- 主流程 ----------

def parse_args():
    parser = argparse.ArgumentParser(
        description="为指定课程生成演示题目与作答记录（P0 冷启动数据）")
    parser.add_argument("--course-id", type=int, default=DEFAULT_COURSE_ID)
    parser.add_argument("--document-id", type=int, default=None,
                        help="缺省=自动取该课程下已抽取完成的第一个文档")
    parser.add_argument("--per-kp", type=int, default=DEFAULT_PER_KP, help="每个知识点的题目数（≤5）")
    parser.add_argument("--max-kp", type=int, default=DEFAULT_MAX_KP, help="参与造题的知识点上限")
    parser.add_argument("--students", type=int, default=DEFAULT_STUDENTS, help="课程内保留的演示学生数")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="作答时间回溯跨度（天）")
    parser.add_argument("--coverage", type=float, default=DEFAULT_COVERAGE, help="作答覆盖率 0-1")
    parser.add_argument("--keep", action="store_true", help="不清理上一轮 IMPORT 数据（追加）")
    parser.add_argument("--clean-only", action="store_true",
                        help="只清理本脚本写入的数据（IMPORT 题/作答/SYSTEM 标记），不生成新数据")
    parser.add_argument("--offline-kp", action="store_true", help="强制用 SQLite 已有 kp_id（跳过 Neo4j）")
    parser.add_argument("--kp-json", default=None,
                        help="离线知识点清单（图谱导出 JSON），Neo4j 未启动时的推荐数据源")
    parser.add_argument("--no-learning", action="store_true", help="不写学习标记")
    parser.add_argument("--seed", type=int, default=20260915, help="随机种子（同种子结果可复现）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    return parser.parse_args()


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    if args.keep and args.clean_only:
        print("✗ --keep 与 --clean-only 语义冲突（一个要保留、一个要清理），请只选其一")
        sys.exit(2)

    print("=" * 72)
    print("演示数据填充：题目 + 作答记录（P0 冷启动）")
    print("=" * 72)

    print(f"[1] 定位课程 course_id={args.course_id}")
    course = resolve_course(args.course_id)

    print(f"[2] 定位文档（课程：{course['course_name']}）")
    doc = resolve_document(args.course_id, args.document_id)
    print(f"    doc_id={doc['doc_id']}  {doc['file_name']}")

    print("[3] 取知识点（图谱事实是题目答案的唯一依据）")
    if args.clean_only:
        kps = []                       # 仅清理模式不需要知识点，跳过图谱查询
        print("    [--clean-only] 跳过（仅清理无需知识点）")
    else:
        kps = fetch_knowledge_points(args.course_id, doc["doc_id"], args.max_kp,
                                     args.offline_kp, args.kp_json)
        print(f"    知识点 {len(kps)} 个")
        if kps and kps[0].get("offline"):
            print("    ⚠ 离线兜底：这批知识点只有 kp_id（无名称/描述/类别），生成的题目为占位题，"
                  "仅用于打通数据链路；图谱恢复后重跑本脚本即可自动替换为真题")
        if not kps:
            print("✗ 没有可用知识点：请先上传文档并完成抽取，或启动 Neo4j 后重试")
            sys.exit(2)

    print("[4] 清理上一轮演示数据" if not args.keep else "[4] 保留上一轮演示数据（--keep）")
    if not args.keep and not args.dry_run:
        removed = clean_previous_seed(args.course_id)
        print(f"    删除 题目 {removed['questions']} / 作答 {removed['answers']} / "
              f"收藏 {removed['favorites']} / 学习标记 {removed['learning']}")
    elif args.dry_run:
        print("    [dry-run] 跳过清理")

    if args.clean_only:
        # 只清理本脚本写入的数据（IMPORT 题 / 其作答 / SYSTEM 学习标记），不生成新数据。
        # 用途：把某个课程恢复到"清理后"的干净状态（如撤销一批占位题）。
        print("\n[--clean-only] 仅清理，不生成新数据。")
        print_overview(args.course_id)
        try:
            db.close()
        except Exception:
            pass
        return

    print("[5] 准备演示学生")
    students = ensure_students(args.course_id, args.students, args.dry_run)
    for s in students:
        print(f"    {s['username']:<14} user_id={s['user_id']:<5} 能力值={s['ability']}")

    print("[6] 生成题目")
    planned = build_questions(kps, args.per_kp, rng)
    by_type, by_diff = {}, {}
    for q in planned:
        by_type[q["q_type"]] = by_type.get(q["q_type"], 0) + 1
        by_diff[q["difficulty"]] = by_diff.get(q["difficulty"], 0) + 1
    print(f"    计划 {len(planned)} 道题，覆盖 {len({q['kp_id'] for q in planned})} 个知识点")
    print(f"    题型分布 {by_type}")
    print(f"    难度分布 {dict(sorted(by_diff.items()))}")
    if not planned:
        print("✗ 一道题都生成不出来（知识点缺 description 或同文档知识点太少）")
        sys.exit(2)

    if args.dry_run:
        print("\n[dry-run] 未写入任何数据；去掉 --dry-run 即真正落库。")
        return

    question_ids = insert_questions(args.course_id, doc["doc_id"], course["teacher_id"], planned)
    print(f"    已写入 question_id = {min(question_ids)}..{max(question_ids)}")

    print("[7] 生成作答记录")
    _, all_rows = sql_db.list_questions(args.course_id, page=1, page_size=100000)
    inserted = set(question_ids)
    rows = [r for r in all_rows if r["question_id"] in inserted]
    # 排除 dry-run 占位学生（user_id = -1）
    real_students = [s for s in students if s["user_id"] > 0]
    result = seed_answers(args.course_id, real_students, rows, args.days, args.coverage, rng)
    print(f"    写入作答 {result['answers']} 条（{len(real_students)} 名学生 × {len(rows)} 道题）")
    if result["matched"] != result["answers"]:
        print(f"    ⚠ {result['answers'] - result['matched']} 条未命中期望正确性（已按真实判分结果落库）")
    for username, st in result["per_student"].items():
        print(f"    {username:<14} 作答 {st['answered']:>4} 条，正确率 {st['rate']}%")

    if not args.no_learning:
        print("[8] 回填学习标记（source=SYSTEM，可被掌握度指标复用）")
        created = seed_learning(args.course_id, doc["doc_id"], result["kp_stats"])
        print(f"    MASTERED {created['MASTERED']} 条 / LEARNING {created['LEARNING']} 条"
              f"（跳过 {created['skipped_manual']} 条已有的 MANUAL 标记，绝不覆盖学生自评）")
    else:
        print("[8] 跳过学习标记（--no-learning）")

    print_overview(args.course_id)

    print("""
下一步验证：
  1) 启动后端：python -m uvicorn app.main:app --reload
  2) 学生端登录（密码 demo1234，演示学生账号见上方 [5]），
     进入「做题练习」可看到错题本 / 统计；「学习总览」可看到掌握情况
  3) 教师端登录课程创建者账号，可在题库 Tab 看到演示题与正确率

说明：再次运行本脚本会先清理上一轮的 IMPORT 题目、对应作答与 SYSTEM 学习标记，
      教师手工建立的题目（source='MANUAL'）不受任何影响。
""")

    try:
        db.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
