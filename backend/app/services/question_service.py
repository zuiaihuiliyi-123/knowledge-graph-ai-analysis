"""
题库服务（教师端题目管理 + 学生端做题练习）

分层约定（与 CourseService / DocumentService 完全一致）：
- 静态方法统一返回 {"ok": bool, "code": int, "message": str, "data": dict}
- 接口层（api/questions.py、api/practice.py）只做参数解析与响应包装

作用域与安全口径（本项目两条硬纪律）：
1. 教师端每个方法都做「课程归属校验」：非主讲时要求为本课程已批准的协作教师，否则 → 4003，
   不能只依赖 require_teacher（否则任何教师都能改别人的题库）。
2. 学生端出题走 _public_view() 白名单投影，**绝不下发 answer / analysis**（防泄题）；
   正确答案与解析只在该题提交后随判分结果返回。

题目作用域：course_id 必填；document_id 可空（空 = 课程通用题，任何文档出题都可见）；
kp_id 可空（逻辑外键指向 Neo4j KnowledgePoint，用于「推荐知识点 → 直接练题」闭环）。
"""
import json

from ..core.database import db
from ..core.sql_database import (
    AUTO_GRADE_TYPES, MANUAL_GRADE_TYPES, sql_db,
)
from .question_recommender import (
    QuestionRecommender, VALID_MODES as VALID_RECOMMEND_MODES,
)

# 题型（Scope B：三型客观题自动判分 + 两型主观题教师批改）
VALID_TYPES = ("SINGLE", "MULTI", "JUDGE", "FILL", "ESSAY")
TYPE_LABELS = {"SINGLE": "单选题", "MULTI": "多选题", "JUDGE": "判断题",
               "FILL": "填空题", "ESSAY": "解答题"}
# 主观题：提交只落库（grade_status=PENDING）不判分，教师批改后才产生分数
MANUAL_TYPES = MANUAL_GRADE_TYPES
# 批改后把 0~100 的分数折算为对/错的阈值（错题本与掌握度依赖 is_correct）
GRADE_PASS_SCORE = 60.0
# 填空题空位数量上限（防误传超大数组/超长答案）
MAX_BLANKS = 20
MAX_BLANK_ANSWER_LEN = 200
MAX_ANSWER_LEN = 4000

MAX_STEM_LEN = 1000
MAX_ANALYSIS_LEN = 1000
# L2 一题多知识点：单题挂载知识点数量上限。
# 作用有二：① 防误传超大数组；② 组卷采取「任一命中即占用配额槽」后，
# 单题挂太多知识点会一次吃掉多个 kp 的配额（见 docs/题库与推荐系统设计.md §12.1 决策 4）。
MAX_KP_PER_QUESTION = 10

# 判断题答案同义归一（"对/正确/是/T/Y/1" 一律视为 true）
_JUDGE_TRUE = {"true", "t", "y", "yes", "1", "对", "正确", "是"}
_JUDGE_FALSE = {"false", "f", "n", "no", "0", "错", "错误", "否"}

_HALF_WIDTH = {ord(c): ord(c) - 0xFEE0
               for c in "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
                        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
                        "０１２３４５６７８９"}


def _loads(value, default):
    """把库内 JSON 文本解析回对象；已是对象则原样返回；解析失败返回 default"""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _normalize_text(value) -> str:
    """答案/选项键归一化：去首尾空白 → 全角转半角 → 小写（仅用于比较，不改变存储值）"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().translate(_HALF_WIDTH).lower()


def _normalize_judge(value) -> str:
    """判断题答案归一化为 "true"/"false"；无法识别时返回归一化文本本身"""
    text = _normalize_text(value)
    if text in _JUDGE_TRUE:
        return "true"
    if text in _JUDGE_FALSE:
        return "false"
    return text


def _answer_keys(answer) -> set:
    """把答案（标量或数组）展开为归一化后的键集合"""
    if answer is None:
        return set()
    raw = answer if isinstance(answer, list) else [answer]
    keys = {_normalize_text(x) for x in raw}
    keys.discard("")
    return keys


def _parse_options(question: dict) -> list:
    """解析 options 字段为 [{"key","text"}]；兼容 ["A. xxx"] 这类纯字符串写法"""
    raw = _loads(question.get("options"), [])
    options = []
    if isinstance(raw, dict):
        raw = [{"key": k, "text": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return options
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            key = str(item.get("key") or chr(65 + i)).strip()
            options.append({"key": key, "text": str(item.get("text") or "").strip()})
        else:
            options.append({"key": chr(65 + i), "text": str(item).strip()})
    return options


def _as_int(value, default: int = 0) -> int:
    """安全转 int（脏数据/缺省返回 default），用于空位编号与分值"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_blanks(question: dict) -> list:
    """解析填空题空位（**教师视角，含参考答案**）。

    存储形态（复用 options 列作「空位定义」）：
      [{"key": 1, "label": "第1空", "hint": "单位：kg", "score": 50, "answer": "浮点数"}]
    兼容简写：options 为字符串数组时按顺序编号，答案取 answer 的同位元素
    （历史/手工导入数据可能出现这种形态）。
    """
    raw = _loads(question.get("options"), [])
    fallback = _loads(question.get("answer"), [])
    if not isinstance(fallback, list):
        fallback = [fallback]
    blanks = []
    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                blanks.append({
                    "key": _as_int(item.get("key"), i + 1) or (i + 1),
                    "label": (str(item.get("label") or f"第{i + 1}空")).strip(),
                    "hint": (str(item.get("hint") or "")).strip(),
                    "score": _as_int(item.get("score"), 0),
                    "answer": (str(item.get("answer") or "")).strip(),
                })
            elif isinstance(item, str):
                blanks.append({
                    "key": i + 1,
                    "label": f"第{i + 1}空",
                    "hint": "",
                    "score": 0,
                    "answer": str(fallback[i] if i < len(fallback) else "").strip(),
                })
    blanks.sort(key=lambda b: b["key"])
    return blanks


def _blanks_public(question: dict) -> list:
    """填空题空位的**学生视角**投影：只给 key/label/hint，绝不下发每空答案"""
    return [{"key": b["key"], "label": b["label"], "hint": b["hint"]}
            for b in _parse_blanks(question)]


def _public_view(question: dict, favorited: bool = False) -> dict:
    """学生视角投影：**白名单**，刻意不包含 answer / analysis（防泄题的关键实现）。

    Scope B：
    - 填空题只下发空位定义（key/label/hint），**每空参考答案绝不下发**；
    - 解答题 options 恒为空数组；
    - auto_graded 供前端决定是否显示"待教师批改"提示（主观题提交后不判分）。
    """
    q_type = question["q_type"]
    if q_type == "FILL":
        options = _blanks_public(question)
    elif q_type == "ESSAY":
        options = []
    else:
        options = _parse_options(question)
    return {
        "question_id": question["question_id"],
        "course_id": question["course_id"],
        "document_id": question.get("document_id"),
        "kp_id": question.get("kp_id"),
        "q_type": q_type,
        "q_type_label": TYPE_LABELS.get(q_type, q_type),
        "stem": question["stem"],
        "options": options,
        "difficulty": question.get("difficulty", 3),
        "auto_graded": q_type in AUTO_GRADE_TYPES,
        "is_favorited": bool(favorited),
    }


def grade(question: dict, user_answer) -> dict:
    """判分分发（Scope B），返回 {is_correct, score, grade_status, pending, correct_answer, analysis}。

    客观题（SINGLE/MULTI/JUDGE）：提交即判分。
    - SINGLE：归一化后严格相等
    - MULTI ：归一化后的键集合严格相等（顺序无关；少选/多选任一情况判错）
    - JUDGE ：布尔归一化后相等（对/正确/true/T/1 等价）

    主观题（FILL/ESSAY）：**不判分** —— 学生的解答只作为记录落库（grade_status=PENDING），
    等教师批改后才产生分数（grade_source 变更为 TEACHER）。
    参考答案与解析也压到批改后才可见：主观题的「解答」本身是教学资源，
    提交即给会让学生失去反思动力，也削弱批改的意义。
    """
    q_type = question["q_type"]
    if q_type in MANUAL_TYPES:
        return {
            "is_correct": False,          # 占位：未批改不判对错，统计口径按 grade_status 过滤
            "score": 0.0,
            "grade_status": "PENDING",
            "pending": True,
            "correct_answer": None,       # 批改后才下发
            "analysis": None,
        }

    correct = _loads(question.get("answer"), None)

    if q_type == "JUDGE":
        expected = _normalize_judge(correct)
        got = _normalize_judge(user_answer)
    elif q_type == "MULTI":
        expected = _answer_keys(correct)
        got = _answer_keys(user_answer)
    else:  # SINGLE
        expected = _normalize_text(correct)
        got = _normalize_text(user_answer)

    is_correct = bool(expected) and expected == got

    return {
        "is_correct": is_correct,
        "score": 100.0 if is_correct else 0.0,
        "grade_status": "GRADED",
        "pending": False,
        "correct_answer": correct,
        "analysis": question.get("analysis") or "",
    }


def validate_question_payload(payload: dict) -> tuple:
    """校验题目字段（新增/修改共用），返回 (ok, message, normalized)。

    normalized 仅含通过校验的字段，供 DAO 直接落库；题型 / 答案规则：
    - SINGLE/MULTI：≥2 个选项且编号唯一非空；答案键必须落在选项内；SINGLE 恰好 1 个答案
    - JUDGE：答案归一化为 true/false
    """
    normalized = {}

    q_type = (payload.get("q_type") or "").strip().upper()
    if q_type not in VALID_TYPES:
        return False, f"题型不合法，仅支持 {list(VALID_TYPES)}", None

    stem = (payload.get("stem") or "").strip()
    if not stem:
        return False, "题干不能为空", None
    if len(stem) > MAX_STEM_LEN:
        return False, f"题干过长（最大 {MAX_STEM_LEN} 字符）", None

    analysis = payload.get("analysis")
    analysis = analysis.strip() if isinstance(analysis, str) else analysis
    if analysis and len(analysis) > MAX_ANALYSIS_LEN:
        return False, f"解析过长（最大 {MAX_ANALYSIS_LEN} 字符）", None

    difficulty = payload.get("difficulty", 3)
    try:
        difficulty = int(difficulty)
    except (TypeError, ValueError):
        return False, "难度必须为 1-5 的整数", None
    if not (1 <= difficulty <= 5):
        return False, "难度应在 1-5 之间", None

    options = payload.get("options") or []
    answer = payload.get("answer")

    if q_type in ("SINGLE", "MULTI"):
        if not isinstance(options, list) or len(options) < 2:
            return False, "选择题至少需要 2 个选项", None
        parsed = _parse_options({"options": options})
        keys = [o["key"] for o in parsed]
        if any(not k for k in keys):
            return False, "选项编号不能为空", None
        if len({_normalize_text(k) for k in keys}) != len(keys):
            return False, "选项编号不能重复", None
        if any(not o["text"] for o in parsed):
            return False, "选项内容不能为空", None
        answer_keys = _answer_keys(answer)
        if not answer_keys:
            return False, "请设置正确答案", None
        invalid = answer_keys - {_normalize_text(k) for k in keys}
        if invalid:
            return False, f"正确答案不在选项范围内: {sorted(invalid)}", None
        if q_type == "SINGLE" and len(answer_keys) != 1:
            return False, "单选题正确答案必须且只能有 1 个", None
        normalized["options"] = parsed
        if q_type == "MULTI":
            # 多选答案统一存为「归一化后的键数组」，保证与判分口径一致
            normalized["answer"] = sorted(answer_keys)
        else:
            normalized["answer"] = answer[0] if isinstance(answer, list) else answer
    elif q_type == "JUDGE":
        judge = _normalize_judge(answer)
        if judge not in ("true", "false"):
            return False, "判断题答案必须为 true/false（或 对/错）", None
        normalized["options"] = []
        normalized["answer"] = judge

    elif q_type == "FILL":
        # 填空题：options 复用为「空位定义」，每空必须给出参考答案（教师批改依据）
        raw = payload.get("options") or []
        if not isinstance(raw, list) or not raw:
            return False, "填空题至少需要 1 个空位", None
        if len(raw) > MAX_BLANKS:
            return False, f"空位过多（最多 {MAX_BLANKS} 个）", None
        blanks, seen_keys = [], set()
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return False, f"第 {i + 1} 个空位格式不合法（应为对象）", None
            blank_answer = str(item.get("answer") or "").strip()
            if not blank_answer:
                return False, f"第 {i + 1} 空未填写参考答案", None
            if len(blank_answer) > MAX_BLANK_ANSWER_LEN:
                return False, (f"第 {i + 1} 空参考答案过长"
                               f"（最大 {MAX_BLANK_ANSWER_LEN} 字符）"), None
            key = _as_int(item.get("key"), i + 1) or (i + 1)
            if key in seen_keys:
                return False, f"空位编号重复：{key}", None
            seen_keys.add(key)
            score = _as_int(item.get("score"), 0)
            if not (0 <= score <= 100):
                return False, f"第 {i + 1} 空分值应在 0-100 之间", None
            blanks.append({
                "key": key,
                "label": str(item.get("label") or f"第{i + 1}空").strip()[:50],
                "hint": str(item.get("hint") or "").strip()[:100],
                "score": score,
                "answer": blank_answer,
            })
        blanks.sort(key=lambda b: b["key"])
        normalized["options"] = blanks
        # 参考答案另存一份数组：与多选答案同构，教师端展示与批改台可直接引用
        normalized["answer"] = [b["answer"] for b in blanks]

    else:  # ESSAY（解答题）
        reference = answer.strip() if isinstance(answer, str) else ""
        if not reference:
            return False, "解答题必须填写参考答案（供教师批改对照）", None
        if len(reference) > MAX_ANSWER_LEN:
            return False, f"参考答案过长（最大 {MAX_ANSWER_LEN} 字符）", None
        normalized["options"] = []
        normalized["answer"] = reference
    normalized["q_type"] = q_type
    normalized["stem"] = stem
    normalized["analysis"] = analysis
    normalized["difficulty"] = difficulty
    normalized["kp_id"] = (payload.get("kp_id") or "").strip() or None
    # L2 一题多知识点：显式传 kp_ids（且非 None）时以**数组为权威**，kp_id 退化为「主知识点」投影。
    # kp_ids=None 视为「本次不改知识点」，继续沿用单值 kp_id 语义（数组清空必须显式传 []）。
    if payload.get("kp_ids") is not None:
        raw_ids = payload.get("kp_ids")
        if not isinstance(raw_ids, (list, tuple, set)):
            return False, "kp_ids 必须为知识点 id 数组", None
        ids, seen = [], set()
        for kp in raw_ids:
            kp = str(kp).strip() if kp is not None else ""
            if kp and kp not in seen:
                seen.add(kp)
                ids.append(kp)
        if len(ids) > MAX_KP_PER_QUESTION:
            return False, f"单题知识点过多（最多 {MAX_KP_PER_QUESTION} 个）", None
        primary = str(payload.get("kp_primary") or "").strip()
        normalized["kp_ids"] = ids
        normalized["kp_primary"] = primary if primary in seen else (ids[0] if ids else None)
        # 投影口径：读侧（前端列表 / 统计 / 兼容期调用方）仍只看 kp_id，必须与数组主知识点一致
        normalized["kp_id"] = normalized["kp_primary"]
    if "document_id" in payload:
        normalized["document_id"] = payload.get("document_id")
    return True, "success", normalized


def _course_for_teacher(course_id: int, user_id: int) -> tuple:
    """课程归属校验：返回 (course, error_dict)；通过时 error_dict 为 None"""
    course = sql_db.get_course(course_id)
    if course is None:
        return None, {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
    if course["teacher_id"] != user_id:
        member = sql_db.get_membership(course_id, user_id)
        if not member or member.get("role") != "teacher" or member.get("status") != "approved":
            return None, {"ok": False, "code": 4003,
                          "message": "无权限：仅该课程的主讲或协作教师可管理其题库"}
    return course, None


def _question_for_teacher(question_id: int, user_id: int) -> tuple:
    """题目归属校验（经 question → course），返回 (question, error_dict)"""
    question = sql_db.get_question(question_id)
    if question is None:
        return None, {"ok": False, "code": 2002, "message": f"题目不存在: question_id={question_id}"}
    _, err = _course_for_teacher(question["course_id"], user_id)
    if err:
        return None, {"ok": False, "code": 4003,
                      "message": "无权限：仅该题目所属课程的主讲或协作教师可操作"}
    return question, None


def _check_document(course_id: int, document_id):
    """document_id 可空（空 = 课程通用题）；非空时校验文档存在且属于该课程。

    返回 (did, error_dict)：did 为 None 表示课程通用题。
    """
    if document_id in (None, "", 0, "0"):
        return None, None
    try:
        did = int(document_id)
    except (TypeError, ValueError):
        return None, {"ok": False, "code": 4001, "message": "参数错误：document_id 必须为整数"}
    doc = sql_db.get_document(did)
    if doc is None:
        return None, {"ok": False, "code": 2002, "message": f"文档不存在: document_id={did}"}
    if doc["course_id"] != course_id:
        return None, {"ok": False, "code": 4003, "message": "无权限：该文档不属于此课程"}
    return did, None


def _check_kp_exists(course_id: int, kp_id: str) -> tuple:
    """校验 kp_id 是否存在于该课程的图谱中，返回 (ok, error_dict, checked)。

    为什么是「课程级」校验（不带 document_id）：教师端的「关联知识点」下拉是
    **聚合该课程全部文档**的图谱节点（见 TeacherView.loadQuestionKpOptions），
    因此题目挂在哪个文档与知识点属于哪个文档允许不一致（课程通用题尤其如此）。

    三种结果：
    - kp_id 为空 → (True, None, True)：题目允许不挂知识点；
    - 图谱可用但查无此点 → (False, 4002, True)：拦住悬空 kp_id
      （手输/复制了别的课程的 id、或图谱重建后旧 id 失效）；
    - 图谱不可用（连接异常等）→ (True, None, False)：**放行**并标记未真正校验，
      避免图库宕机时教师完全无法建题（返回值里 checked=False 会透出给前端）。
    """
    kp_id = (kp_id or "").strip()
    if not kp_id:
        return True, None, True
    try:
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, kp_id: $kp_id}) RETURN n.kp_id AS kp_id",
            {"cid": course_id, "kp_id": kp_id},
        )
    except Exception:
        return True, None, False

    if not recs:
        return False, {
            "ok": False, "code": 4002,
            "message": (f"知识点不存在：course_id={course_id}, kp_id={kp_id}"
                        "（请从「关联知识点」下拉中选择本课程图谱中的知识点）"),
        }, True
    return True, None, True


def _check_kps_exist(course_id: int, kp_ids) -> tuple:
    """批量校验知识点是否存在于该课程图谱，返回 (ok, error_dict, checked)。

    与 _check_kp_exists 同一策略（图谱可用但查无此点 → 4002；图谱不可用 → 放行并 checked=False），
    区别是用**一次** Cypher 的 `IN` 查询代替 N 次单点查询——一题多 KP 后这点很关键：
    逐点校验会让「一题挂 6 个知识点」的保存动作打 6 次图库往返。
    """
    ids = [str(k).strip() for k in (kp_ids or []) if k and str(k).strip()]
    if not ids:
        return True, None, True
    try:
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) WHERE n.kp_id IN $ids "
            "RETURN n.kp_id AS kp_id",
            {"cid": course_id, "ids": ids},
        )
    except Exception:
        return True, None, False

    found = {r.get("kp_id") for r in recs}
    missing = [k for k in ids if k not in found]
    if missing:
        return False, {
            "ok": False, "code": 4002,
            "message": (f"知识点不存在：course_id={course_id}, kp_id={'、'.join(missing)}"
                        "（请从「关联知识点」下拉中选择本课程图谱中的知识点）"),
        }, True
    return True, None, True


def _teacher_view(question: dict, stats: dict = None, favorite_count: int = 0) -> dict:
    """教师视角：在库行基础上补充解析后的 options/answer 与作答统计（学生接口绝不复用本函数）。

    Scope B：填空题用 _parse_blanks（**保留每空参考答案**，教师批改对照需要），
    解答题的 answer 即参考答案正文。
    """
    data = dict(question)
    if question["q_type"] == "FILL":
        data["options"] = _parse_blanks(question)
    else:
        data["options"] = _parse_options(question)
    data["answer"] = _loads(question.get("answer"), question.get("answer"))
    if question["q_type"] == "JUDGE":
        # 兼容历史行：旧数据里判断题答案可能被存成裸 JSON（回读为布尔 True），
        # 这里统一归一化为 "true"/"false" 字符串，保证教师端展示口径一致。
        data["answer"] = _normalize_judge(data["answer"])
    data["q_type_label"] = TYPE_LABELS.get(question["q_type"], question["q_type"])
    data["auto_graded"] = question["q_type"] in AUTO_GRADE_TYPES
    st = stats or {}
    attempts = st.get("attempts", 0)
    correct = st.get("correct", 0)
    data["attempts"] = attempts
    data["correct_count"] = correct
    data["correct_rate"] = round(correct / attempts * 100, 1) if attempts else 0.0
    data["favorite_count"] = favorite_count
    return data


class QuestionService:
    """教师端题库管理（每个方法都做课程归属校验，越权返回 4003）"""

    @staticmethod
    def create_question(user_id: int, course_id: int, payload: dict) -> dict:
        """新增题目（course_id 必传；document_id 可空 = 课程通用题）"""
        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err

        ok, message, normalized = validate_question_payload(payload)
        if not ok:
            return {"ok": False, "code": 1001, "message": message}

        did, err = _check_document(course_id, payload.get("document_id"))
        if err:
            return err

        # 知识点完整性校验（图谱可用时拦住悬空知识点；图库不可用时放行并标记 kp_checked=False）
        # L2：一题多 KP 走批量校验（一次图库往返）；单值退回原点查，两者口径一致
        kp_ids = normalized.get("kp_ids") if "kp_ids" in normalized else None
        if kp_ids is not None:
            ok_kp, kp_err, kp_checked = _check_kps_exist(course_id, kp_ids)
        else:
            ok_kp, kp_err, kp_checked = _check_kp_exists(course_id, normalized["kp_id"])
        if not ok_kp:
            return kp_err

        question_id = sql_db.create_question(
            course_id=course_id, document_id=did, kp_id=normalized["kp_id"],
            kp_ids=kp_ids, kp_primary=normalized.get("kp_primary"),
            q_type=normalized["q_type"], stem=normalized["stem"],
            options=normalized["options"], answer=normalized["answer"],
            analysis=normalized["analysis"], difficulty=normalized["difficulty"],
            created_by=user_id, source="MANUAL",
        )
        return {"ok": True, "code": 0, "message": "success", "data": {
            "question_id": question_id,
            "course_id": course_id,
            "document_id": did,
            "created": True,
            "kp_checked": kp_checked,
            "question": _teacher_view(sql_db.get_question(question_id)),
        }}

    @staticmethod
    def update_question(user_id: int, question_id: int, payload: dict) -> dict:
        """修改题目：未传的字段沿用原值（支持只改解析、只改难度等局部编辑）。

        与 document_id 同一套「清空」语义：**显式传 kp_id=null 表示解除知识点关联**
        （前端表单就是这么发的），未传该键才保持原值。
        """
        question, err = _question_for_teacher(question_id, user_id)
        if err:
            return err

        merged = {
            "q_type": payload.get("q_type") if payload.get("q_type") is not None else question["q_type"],
            "stem": payload.get("stem") if payload.get("stem") is not None else question["stem"],
            "options": payload.get("options") if payload.get("options") is not None
                       else (_parse_blanks(question) if question["q_type"] == "FILL"
                             else _parse_options(question)),
            "answer": payload.get("answer") if payload.get("answer") is not None
                      else _loads(question.get("answer"), question.get("answer")),
            "analysis": payload.get("analysis") if payload.get("analysis") is not None
                        else question.get("analysis"),
            "difficulty": payload.get("difficulty") if payload.get("difficulty") is not None
                          else question.get("difficulty", 3),
            # kp_id 可空：只有「传了该键」才改（含显式 null = 清空关联）
            "kp_id": payload.get("kp_id") if "kp_id" in payload else question.get("kp_id"),
        }
        # L2：显式传 kp_ids 时把它带进校验（数组为权威，kp_id 退化为主知识点投影）
        if "kp_ids" in payload:
            merged["kp_ids"] = payload.get("kp_ids")
            if "kp_primary" in payload:
                merged["kp_primary"] = payload.get("kp_primary")
        if "document_id" in payload:
            merged["document_id"] = payload.get("document_id")

        ok, message, normalized = validate_question_payload(merged)
        if not ok:
            return {"ok": False, "code": 1001, "message": message}

        did = question.get("document_id")
        if "document_id" in payload:
            did, err = _check_document(question["course_id"], payload.get("document_id"))
            if err:
                return err
            # document_id 的「清空」语义用独立方法表达（update_question 对 None = 不修改）
            sql_db.set_question_document(question_id, did)

        # 知识点完整性校验：只在「本次显式传入知识点」时执行——
        # 历史数据里可能已有悬空 kp_id，教师仅改解析/难度时不应被拦住；
        # 显式传 null / 空数组（清空关联）也不校验。
        kp_checked = True
        if "kp_ids" in payload:
            ok_kp, kp_err, kp_checked = _check_kps_exist(question["course_id"],
                                                         normalized.get("kp_ids"))
            if not ok_kp:
                return kp_err
        elif payload.get("kp_id") is not None:
            ok_kp, kp_err, kp_checked = _check_kp_exists(question["course_id"], normalized["kp_id"])
            if not ok_kp:
                return kp_err

        # L2 关键：**只有本次真的改了知识点，才传 kp 字段**。
        # 旧实现无条件传 kp_id，会让「教师只改难度/解析」把多 KP 题塌缩成单 KP
        # （sql_db.update_question 对 kp 字段的语义是「全量替换」）。
        update_fields = dict(
            q_type=normalized["q_type"],
            stem=normalized["stem"],
            options=normalized["options"],
            answer=normalized["answer"],
            analysis=normalized["analysis"] or "",
            difficulty=normalized["difficulty"],
        )
        if "kp_ids" in payload:
            update_fields["kp_ids"] = normalized.get("kp_ids") or []
            update_fields["kp_primary"] = normalized.get("kp_primary")
        elif "kp_id" in payload:
            update_fields["kp_id"] = normalized["kp_id"] or ""
        sql_db.update_question(question_id, **update_fields)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "question_id": question_id,
            "updated": True,
            "kp_checked": kp_checked,
            "question": _teacher_view(sql_db.get_question(question_id)),
        }}

    @staticmethod
    def set_active(user_id: int, question_id: int, is_active: bool) -> dict:
        """启用/停用题目（停用 = 软删：从出题池移除但保留历史答题记录）"""
        _, err = _question_for_teacher(question_id, user_id)
        if err:
            return err
        sql_db.set_question_active(question_id, is_active)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "question_id": question_id,
            "is_active": bool(is_active),
        }}

    @staticmethod
    def delete_question(user_id: int, question_id: int) -> dict:
        """删除题目：已被作答过的题目只做软删（保护学生答题记录），否则物理删除"""
        _, err = _question_for_teacher(question_id, user_id)
        if err:
            return err

        answered = sql_db.count_answers_by_question(question_id)
        if answered > 0:
            sql_db.set_question_active(question_id, False)
            return {"ok": True, "code": 0, "message": "success", "data": {
                "question_id": question_id,
                "deleted": False,
                "soft_deleted": True,
                "answered_count": answered,
                "hint": f"该题已有 {answered} 条学生作答记录，已停用（移出出题池）而未物理删除",
            }}

        removed = sql_db.delete_question(question_id)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "question_id": question_id,
            "deleted": removed > 0,
            "soft_deleted": False,
            "answered_count": 0,
        }}

    @staticmethod
    def list_questions(user_id: int, course_id: int, document_id=None, kp_id: str = None,
                       q_type: str = None, keyword: str = None, is_active=None,
                       page: int = 1, page_size: int = 10) -> dict:
        """题库列表（教师视角，含答案/解析/作答统计/收藏数），分页 + 多条件筛选"""
        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err

        did = None
        if document_id not in (None, "", 0, "0"):
            did, err = _check_document(course_id, document_id)
            if err:
                return err

        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        total, rows = sql_db.list_questions(
            course_id, document_id=did, kp_id=(kp_id or "").strip() or None,
            q_type=(q_type or "").strip().upper() or None,
            keyword=(keyword or "").strip() or None, is_active=is_active,
            page=page, page_size=page_size,
        )
        stats = sql_db.question_answer_stats(course_id)
        fav_counts = sql_db.count_question_favorites_grouped(course_id)
        items = [
            _teacher_view(r, stats.get(r["question_id"]), fav_counts.get(r["question_id"], 0))
            for r in rows
        ]
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "document_id": did,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }}

    @staticmethod
    def get_question(user_id: int, question_id: int) -> dict:
        """题目详情（教师视角）"""
        question, err = _question_for_teacher(question_id, user_id)
        if err:
            return err
        stats = sql_db.question_answer_stats(question["course_id"]).get(question_id)
        fav = sql_db.count_question_favorites_grouped(question["course_id"]).get(question_id, 0)
        return {"ok": True, "code": 0, "message": "success",
                "data": _teacher_view(question, stats, fav)}

    @staticmethod
    def stats(user_id: int, course_id: int) -> dict:
        """题库总览：题量 / 启用停用 / 题型分布 / 作答总数 / 平均正确率 / 收藏总数"""
        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err

        _, rows = sql_db.list_questions(course_id, page=1, page_size=10000)
        by_type = {t: 0 for t in VALID_TYPES}
        active = 0
        for r in rows:
            by_type[r["q_type"]] = by_type.get(r["q_type"], 0) + 1
            if r["is_active"]:
                active += 1

        answers = sql_db.list_answer_records_by_course(course_id)
        # 口径（Scope B）：正确率只算已批改记录；未批改的主观题单列 pending_count，
        # 并把批改进度（grading）一并下发，供教师端「批改」入口显示角标。
        graded = [a for a in answers if (a.get("grade_status") or "GRADED") == "GRADED"]
        correct = sum(1 for a in graded if a["is_correct"])
        fav_total = sum(sql_db.count_question_favorites_grouped(course_id).values())

        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "total": len(rows),
            "active_count": active,
            "inactive_count": len(rows) - active,
            "by_type": by_type,
            "answer_count": len(answers),
            "graded_count": len(graded),
            "pending_count": len(answers) - len(graded),
            "correct_rate": round(correct / len(graded) * 100, 1) if graded else 0.0,
            "student_count": len({a["user_id"] for a in answers}),
            "favorite_total": fav_total,
            "grading": sql_db.answer_grading_summary(course_id),
        }}

    @staticmethod
    def coverage(user_id: int, course_id: int, document_id=None) -> dict:
        """知识点题目覆盖率：无题知识点清单 + 悬空 kp_id（教师「该补哪些题」的指引）。

        作用域：course_id 必填；传 document_id 时只统计「该文档题目 + 课程通用题」
        （与出题/列表口径一致），知识点侧按该文档过滤（图谱按 course+document 隔离）。

        统计口径与价值：
        - 只数**启用中**的题目（停用的题出不出来，等同于没有题）；
        - `unmatched`：本作用域内还没有任何启用题的知识点 → 教师补题清单；
        - `dangling`：题目引用了「本课程图谱中已不存在」的 kp_id（图谱重建/跨课程复制导致）
          → 这类题在「按知识点出题」里永远选不到，需要教师修正；
        - `unlinked_question_count`：压根没挂知识点的题目数。

        图谱不可用时降级（`graph_available=False`）：仍返回 SQLite 能算出的题量统计，
        但给不出"无题知识点 / 悬空 kp_id"（两者都需要图谱），不整页报错。
        """
        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err

        did = None
        if document_id not in (None, "", 0, "0"):
            did, err = _check_document(course_id, document_id)
            if err:
                return err

        q_by_kp, unlinked = sql_db.count_questions_grouped_by_kp(
            course_id, document_id=did, only_active=True,
        )

        graph_available, kps = True, []
        try:
            cypher = ("MATCH (n:KnowledgePoint {course_id: $cid"
                      + (", document_id: $did" if did is not None else "") + "}) "
                      "RETURN n.kp_id AS kp_id, n.name AS name, n.category AS category, "
                      "       n.document_id AS document_id")
            params = {"cid": course_id}
            if did is not None:
                params["did"] = did
            kps = db.query(cypher, params)
        except Exception:
            graph_available = False

        items, unmatched = [], []
        for kp in kps:
            kp_id = kp.get("kp_id")
            count = q_by_kp.get(kp_id, 0)
            item = {
                "kp_id": kp_id,
                "name": kp.get("name") or kp_id,
                "category": kp.get("category") or "",
                "document_id": kp.get("document_id"),
                "question_count": count,
                "covered": count > 0,
            }
            items.append(item)
            if count == 0:
                unmatched.append(item)
        unmatched.sort(key=lambda x: (x["category"], x["name"]))

        dangling = []
        if graph_available:
            known_ids = {kp.get("kp_id") for kp in kps}
            dangling = [
                {"kp_id": kp_id, "question_count": cnt}
                for kp_id, cnt in sorted(q_by_kp.items(), key=lambda x: -x[1])
                if kp_id not in known_ids
            ]

        total_kp = len(kps)
        covered_kp = total_kp - len(unmatched)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "document_id": did,
            "graph_available": graph_available,
            "total_kp": total_kp,
            "kp_with_question": covered_kp,
            "kp_without_question": len(unmatched),
            "coverage_rate": round(covered_kp / total_kp * 100, 1) if total_kp else 0.0,
            "question_count": sum(q_by_kp.values()),
            "unlinked_question_count": unlinked,
            "dangling_count": len(dangling),
            "dangling": dangling[:50],
            "unmatched": unmatched,
            "items": items,
        }}

    @staticmethod
    def kp_candidates(user_id: int, question_id: int, top_k: int = 5) -> dict:
        """单题知识点候选（教师端「自动标注」按钮）

        内部走三层证据（字面匹配 / 向量召回 / 图谱扩展）并做融合打分；
        任一路不可用都降级而不是报错，`meta` 里如实标注（graph_available / vector_available）。
        """
        from .kp_labeler import label_one            # 延迟导入：避免与 embedding 形成导入顺序耦合

        question, err = _question_for_teacher(question_id, user_id)
        if err:
            return err
        top_k = min(max(1, int(top_k or 5)), 20)
        res = label_one(question, top_k=top_k)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "question_id": question_id,
            "stem": question.get("stem") or "",
            "q_type": question.get("q_type"),
            "current_kp_id": question.get("kp_id") or "",
            "candidates": res["candidates"],
            "meta": res["meta"],
        }}

    @staticmethod
    def auto_label(user_id: int, course_id: int, document_id=None, question_ids=None,
                   only_missing: bool = True, apply: bool = False, top_k: int = 3,
                   apply_threshold: float = 0.6) -> dict:
        """批量知识点标注：默认只出建议（apply=False）；apply=True 时只写达到阈值的题

        - 默认 `only_missing=True`：只处理尚未挂知识点的题（不覆盖教师已有的判断）；
        - 写库阈值默认 0.60（`kp_labeler.APPLY_MIN_SCORE`），且候选必须在本课程图谱清单内。
        """
        from .kp_labeler import label_questions

        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err
        did, err = _check_document(course_id, document_id)
        if err:
            return err
        try:
            top_k = min(max(1, int(top_k or 3)), 10)
        except (TypeError, ValueError):
            top_k = 3
        try:
            threshold = float(apply_threshold)
        except (TypeError, ValueError):
            threshold = 0.6
        return label_questions(
            course_id, document_id=did, question_ids=question_ids,
            only_missing=bool(only_missing), apply=bool(apply), top_k=top_k,
            apply_threshold=min(max(threshold, 0.0), 1.0),
        )

    @staticmethod
    async def import_preview(user_id: int, course_id: int, document_id, max_questions=None) -> dict:
        """解析课程文档 → 题目候选**预览**（不写库；Scope D / P2）

        走确定性规则解析（零 LLM 成本）；每道题带 q_type/options/answer/warnings/
        import_status/confidence，教师在预览里可编辑后再提交。
        """
        from .document_parser import DocumentParser
        from .question_importer import parse_text
        from .document_service import _resolve_stored_path

        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err
        did, err = _check_document(course_id, document_id)
        if err:
            return err
        doc = sql_db.get_document(did)
        try:
            text = await DocumentParser.parse(_resolve_stored_path(doc["file_path"]))
        except Exception as e:
            return {"ok": False, "code": 2005, "message": f"文档解析失败：{e}"}
        result = parse_text(text, max_questions=max_questions)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "document_id": did,
            "file_name": doc.get("file_name"), "source": result["source"],
            "stats": result["stats"], "items": result["items"],
        }}

    @staticmethod
    def import_commit(user_id: int, course_id: int, document_id, items: list,
                      activate: bool = False) -> dict:
        """把（教师确认/编辑过的）候选题目入库为**暂存题**（默认 is_active=0）

        逐题复用 validate_question_payload 校验：主观题缺参考答案、选项不足等一律拒绝并回报原因，
        绝不把不合格内容写进题库（宁缺毋滥）。
        """
        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err
        did, err = _check_document(course_id, document_id)
        if err:
            return err
        if not isinstance(items, list) or not items:
            return {"ok": False, "code": 4001, "message": "参数错误：items 不能为空"}
        doc = sql_db.get_document(did)
        batch_id = sql_db.create_import_batch(
            course_id, did, (doc or {}).get("file_name") or "", source="RULE",
            total=len(items), created_by=user_id, meta={"activate": bool(activate)},
        )

        imported, skipped, needs_review = 0, [], 0
        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                skipped.append({"index": idx, "reason": "条目格式非法"})
                continue
            payload = {
                "q_type": raw.get("q_type"), "stem": raw.get("stem"),
                "options": raw.get("options") or [], "answer": raw.get("answer"),
                "analysis": raw.get("analysis") or "", "difficulty": raw.get("difficulty") or 3,
                "kp_id": raw.get("kp_id"),
            }
            # L2：导入条目可携带多知识点（导入解析器/复核阶段回填），仅当确为数组时才带入校验
            if isinstance(raw.get("kp_ids"), (list, tuple)):
                payload["kp_ids"] = raw.get("kp_ids")
                if raw.get("kp_primary"):
                    payload["kp_primary"] = raw.get("kp_primary")
            ok, message, normalized = validate_question_payload(payload)
            if not ok:
                skipped.append({"index": idx, "number": raw.get("number"), "reason": message})
                continue
            status = raw.get("import_status") or "READY"
            if status == "READY" and raw.get("warnings"):
                status = "NEEDS_REVIEW"
            if status != "READY":
                needs_review += 1
            sql_db.create_question(
                course_id=course_id, document_id=did, kp_id=normalized["kp_id"],
                kp_ids=normalized.get("kp_ids") if "kp_ids" in normalized else None,
                kp_primary=normalized.get("kp_primary"),
                q_type=normalized["q_type"], stem=normalized["stem"],
                options=normalized["options"], answer=normalized["answer"],
                analysis=normalized["analysis"], difficulty=normalized["difficulty"],
                created_by=user_id, source="IMPORT", is_active=1 if activate else 0,
                import_batch_id=batch_id, import_status=status,
            )
            imported += 1

        sql_db.update_import_batch(batch_id, imported=imported, needs_review=needs_review,
                                   status="COMMITTED")
        return {"ok": True, "code": 0, "message": "success", "data": {
            "batch_id": batch_id, "course_id": course_id, "document_id": did,
            "total": len(items), "imported": imported, "skipped": skipped,
            "needs_review": needs_review, "activated": bool(activate),
            "hint": "导入的题目默认「停用」进入暂存区，请在题库列表中复核后启用",
        }}

    @staticmethod
    def import_batch_detail(user_id: int, batch_id: str) -> dict:
        """批次明细：批次信息 + 已入库题目（含答案，教师视角）"""
        batch = sql_db.get_import_batch(batch_id)
        if batch is None:
            return {"ok": False, "code": 2002, "message": f"导入批次不存在: {batch_id}"}
        _, err = _course_for_teacher(batch["course_id"], user_id)
        if err:
            return err
        total, rows = sql_db.list_questions_by_batch(batch_id, page=1, page_size=500)
        stats = sql_db.question_answer_stats(batch["course_id"])
        return {"ok": True, "code": 0, "message": "success", "data": {
            "batch": batch, "total": total,
            "items": [_teacher_view(r, stats.get(r["question_id"])) for r in rows],
        }}

    @staticmethod
    def favorites(user_id: int, course_id: int, question_id: int = None) -> dict:
        """题目收藏情况：哪些学生收藏了哪道题（教师端「查看题目收藏情况」）"""
        _, err = _course_for_teacher(course_id, user_id)
        if err:
            return err

        rows = sql_db.list_question_favorite_users(course_id, question_id)
        items = []
        for r in rows:
            q = sql_db.get_question(r["question_id"]) or {}
            u = sql_db.get_user_by_id(r["user_id"]) or {}
            items.append({
                "question_id": r["question_id"],
                "stem": q.get("stem", ""),
                "q_type": q.get("q_type"),
                "q_type_label": TYPE_LABELS.get(q.get("q_type"), q.get("q_type", "")),
                "student_id": r["user_id"],
                "student_name": u.get("display_name") or u.get("username") or str(r["user_id"]),
                "username": u.get("username", ""),
                "created_at": r["created_at"],
            })
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "total": len(items),
            "items": items,
        }}


class PracticeService:
    """学生端做题练习（出题 / 判分 / 错题本 / 题目收藏）。

    可见性口径：与「文档、图谱」保持一致——学生可见课程内全部启用题目
    （系统当前无选课关系表，故不做「仅已选课程」限制；若要收紧，只需改本类）。
    """

    @staticmethod
    def get_questions(user_id: int, course_id: int, document_id=None, kp_id: str = None,
                      q_type: str = None, count: int = 10) -> dict:
        """出题：随机取启用题目；**返回体不含 answer/analysis**（提交后才下发）"""
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}

        did = None
        if document_id not in (None, "", 0, "0"):
            try:
                did = int(document_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": 4001, "message": "参数错误：document_id 必须为整数"}

        try:
            count = int(count or 10)
        except (TypeError, ValueError):
            count = 10
        count = min(max(1, count), 50)

        rows = sql_db.list_practice_questions(
            course_id, document_id=did, kp_id=(kp_id or "").strip() or None,
            q_type=(q_type or "").strip().upper() or None, limit=count,
        )
        fav_ids = sql_db.list_question_favorite_ids(user_id, course_id)
        items = [_public_view(r, r["question_id"] in fav_ids) for r in rows]
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "document_id": did,
            "count": len(items),
            "total_in_bank": sql_db.count_questions_by_course(course_id),
            "items": items,
        }}

    @staticmethod
    def recommend_questions(user_id: int, course_id: int, document_id=None, kp_id: str = None,
                            q_type: str = None, count: int = 10, mode: str = "mixed",
                            seed: int = None) -> dict:
        """智能推荐出题：按 8 个信号选卷（薄弱度 / 遗忘到期 / 难度适配 / 新颖度 /
        最近答错 / 图谱重要性 / 题目区分度 / 知识点与题型配额）。

        - 与 `get_questions` 同一套作用域与**防泄题纪律**：返回体走 `_public_view()` 白名单投影；
        - 每题附带 `reason`（推荐理由）与 `bucket`（分层：薄弱强化/复习巩固/路径新知识/进阶提升），
          前端可直接展示"为什么推这题"；
        - 推荐过程若异常，**降级为随机出题**（meta.degraded=True），保证学生端不至于点不动。
        """
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}

        did = None
        if document_id not in (None, "", 0, "0"):
            try:
                did = int(document_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": 4001, "message": "参数错误：document_id 必须为整数"}

        try:
            count = int(count or 10)
        except (TypeError, ValueError):
            count = 10
        count = min(max(1, count), 50)

        mode = (mode or "mixed").strip().lower()
        if mode not in VALID_RECOMMEND_MODES:
            return {"ok": False, "code": 4001,
                    "message": f"参数错误：mode 取值应为 {sorted(VALID_RECOMMEND_MODES)}"}

        degraded = False
        try:
            result = QuestionRecommender.recommend(
                user_id, course_id, document_id=did, kp_id=kp_id, q_type=q_type,
                count=count, mode=mode, seed=seed,
            )
        except Exception:
            degraded = True
            result = {
                "items": [{"row": r} for r in sql_db.list_practice_questions(
                    course_id, document_id=did, kp_id=(kp_id or "").strip() or None,
                    q_type=(q_type or "").strip().upper() or None, limit=count)],
                "meta": {"mode": mode, "buckets": {}, "mastery_available": False,
                         "graph_available": False},
            }

        fav_ids = sql_db.list_question_favorite_ids(user_id, course_id)
        items = []
        for it in result["items"]:
            view = _public_view(it["row"], it["row"]["question_id"] in fav_ids)
            view.update({
                "reason": it.get("reason"),
                "bucket": it.get("bucket"),
                "bucket_label": it.get("bucket_label"),
                "kp_name": it.get("kp_name"),
                "mastery": it.get("mastery"),
                "attempts": it.get("attempts"),
                "score": it.get("score"),
            })
            items.append(view)

        meta = dict(result.get("meta") or {})
        meta.update({"count": len(items), "degraded": degraded,
                     "total_in_bank": sql_db.count_questions_by_course(course_id),
                     "requested": count})
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "document_id": did, "count": len(items),
            "total_in_bank": meta["total_in_bank"], "items": items, "meta": meta,
        }}

    @staticmethod
    def submit(user_id: int, question_id: int, user_answer) -> dict:
        """提交作答：服务端判分 → 落答题记录 → 返回正确答案与解析（答案解析仅此路径下发）"""
        question = sql_db.get_question(question_id)
        if question is None:
            return {"ok": False, "code": 2002, "message": f"题目不存在: question_id={question_id}"}
        if not question["is_active"]:
            return {"ok": False, "code": 2004, "message": "该题已停用，无法作答"}

        result = grade(question, user_answer)
        pending = bool(result.get("pending"))
        record_id = sql_db.add_answer_record(
            user_id=user_id, course_id=question["course_id"],
            document_id=question.get("document_id"), question_id=question_id,
            user_answer=user_answer, is_correct=result["is_correct"],
            score=result["score"], grade_source="AUTO",
            grade_status=result.get("grade_status", "GRADED"),
        )
        return {"ok": True, "code": 0, "message": "success", "data": {
            "record_id": record_id,
            "question_id": question_id,
            "course_id": question["course_id"],
            "document_id": question.get("document_id"),
            "kp_id": question.get("kp_id"),
            "user_answer": user_answer,
            # 主观题未批改：不给分数也不判对错（前端据此显示"已提交，等待教师批改"）
            "is_correct": None if pending else result["is_correct"],
            "score": None if pending else result["score"],
            "grade_status": result.get("grade_status", "GRADED"),
            "pending": pending,
            "correct_answer": result["correct_answer"],
            "analysis": result["analysis"] or "",
        }}

    @staticmethod
    def records(user_id: int, course_id: int = None, document_id=None,
                only_wrong: bool = False, limit: int = 100) -> dict:
        """我的答题记录（时间倒序，含题干与正确答案——本人已作答过，可回看）"""
        did = None
        if document_id not in (None, "", 0, "0"):
            try:
                did = int(document_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": 4001, "message": "参数错误：document_id 必须为整数"}

        rows = sql_db.list_answer_records(user_id, course_id=course_id, document_id=did,
                                          only_wrong=only_wrong,
                                          include_pending=not only_wrong)
        items = []
        for r in rows[:max(1, limit)]:
            q = sql_db.get_question(r["question_id"])
            pending = (r.get("grade_status") or "GRADED") == "PENDING"
            items.append({
                "record_id": r["record_id"],
                "question_id": r["question_id"],
                "course_id": r["course_id"],
                "document_id": r.get("document_id"),
                "stem": (q or {}).get("stem", "（题目已删除）"),
                "q_type": (q or {}).get("q_type"),
                "q_type_label": TYPE_LABELS.get((q or {}).get("q_type"), ""),
                "user_answer": _loads(r.get("user_answer"), r.get("user_answer")),
                "is_correct": None if pending else bool(r["is_correct"]),
                "score": None if pending else r["score"],
                "grade_status": "PENDING" if pending else "GRADED",
                "pending": pending,
                "comment": r.get("comment"),
                "graded_at": r.get("graded_at"),
                "answered_at": r["answered_at"],
                # 待批改的主观题不下发参考答案与解析（与提交接口同一口径）
                "correct_answer": None if pending else _loads((q or {}).get("answer"), None),
                "analysis": "" if pending else ((q or {}).get("analysis") or ""),
            })
        graded = [r for r in rows if (r.get("grade_status") or "GRADED") == "GRADED"]
        correct = sum(1 for r in graded if r["is_correct"])
        return {"ok": True, "code": 0, "message": "success", "data": {
            "total": len(rows),
            "graded_count": len(graded),
            "pending_count": len(rows) - len(graded),
            "correct_count": correct,
            "correct_rate": round(correct / len(graded) * 100, 1) if graded else 0.0,
            "items": items,
        }}

    @staticmethod
    def wrong_book(user_id: int, course_id: int, document_id=None) -> dict:
        """错题本：按题取「最近一次答错」的记录，并回填知识点名称（供「回图谱重学」）"""
        did = None
        if document_id not in (None, "", 0, "0"):
            try:
                did = int(document_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": 4001, "message": "参数错误：document_id 必须为整数"}

        rows = sql_db.list_answer_records(user_id, course_id=course_id,
                                          document_id=did, only_wrong=True,
                                          include_pending=False)
        wrong_counts = {}
        for r in rows:
            wrong_counts[r["question_id"]] = wrong_counts.get(r["question_id"], 0) + 1

        # rows 已按 record_id DESC（时间倒序），首次出现的即该题最近一次错误
        seen, items = set(), []
        for r in rows:
            qid = r["question_id"]
            if qid in seen:
                continue
            seen.add(qid)
            q = sql_db.get_question(qid)
            if q is None or not q["is_active"]:
                continue
            view = _public_view(q)
            view.update({
                "wrong_count": wrong_counts[qid],
                "last_wrong_at": r["answered_at"],
                "last_user_answer": _loads(r.get("user_answer"), r.get("user_answer")),
                "correct_answer": _loads(q.get("answer"), None),
                "analysis": q.get("analysis") or "",
                "kp_name": None,
                # Scope B 批改结果可见性：主观题由教师批改后才有分值/评语（客观题为 AUTO 判分）
                "last_score": r.get("score"),
                "last_comment": r.get("comment") or "",
                "grade_source": r.get("grade_source"),
            })
            items.append(view)

        # 知识点名称（Neo4j 不可用时降级为 None，不影响错题本可用性）
        kp_ids = [it["kp_id"] for it in items if it.get("kp_id")]
        if kp_ids:
            try:
                recs = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $cid}) WHERE n.kp_id IN $ids "
                    "RETURN n.kp_id AS kp_id, n.name AS name",
                    {"cid": course_id, "ids": kp_ids},
                )
                kp_names = {r["kp_id"]: r["name"] for r in recs}
                for it in items:
                    it["kp_name"] = kp_names.get(it.get("kp_id"))
            except Exception:
                pass

        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "document_id": did,
            "total": len(items),
            "items": items,
        }}

    @staticmethod
    def stats(user_id: int, course_id: int, document_id=None) -> dict:
        """我的练习统计：累计作答 / 正确率 / 错题数 / 收藏数（学生总览 KPI 用）"""
        did = None
        if document_id not in (None, "", 0, "0"):
            try:
                did = int(document_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": 4001, "message": "参数错误：document_id 必须为整数"}

        rows = sql_db.list_answer_records(user_id, course_id=course_id, document_id=did)
        # 口径（Scope B）：正确率与错题数只看「已批改」记录——主观题提交后是 PENDING，
        # 若混入统计会把"老师还没批"当成"答错"，学生会看到莫名下降的正确率。
        graded = [r for r in rows if (r.get("grade_status") or "GRADED") == "GRADED"]
        pending = [r for r in rows if (r.get("grade_status") or "GRADED") == "PENDING"]
        correct = sum(1 for r in graded if r["is_correct"])
        wrong_questions = {r["question_id"] for r in graded if not r["is_correct"]}
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "document_id": did,
            "answer_count": len(rows),
            "graded_count": len(graded),
            "pending_count": len(pending),
            "correct_count": correct,
            "correct_rate": round(correct / len(graded) * 100, 1) if graded else 0.0,
            "wrong_question_count": len(wrong_questions),
            "favorite_count": len(sql_db.list_question_favorites(user_id, course_id)),
        }}

    @staticmethod
    def list_favorites(user_id: int, course_id: int) -> dict:
        """我的题目收藏（含题面，便于直接重做）"""
        if sql_db.get_course(course_id) is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}

        items = []
        for r in sql_db.list_question_favorites(user_id, course_id):
            q = sql_db.get_question(r["question_id"])
            if q is None:
                continue
            view = _public_view(q, favorited=True)
            view["favorited_at"] = r["created_at"]
            items.append(view)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "total": len(items),
            "items": items,
        }}

    @staticmethod
    def favorite(user_id: int, course_id: int, question_id: int) -> dict:
        """收藏题目（幂等：重复收藏不产生重复记录）"""
        question = sql_db.get_question(question_id)
        if question is None:
            return {"ok": False, "code": 2002, "message": f"题目不存在: question_id={question_id}"}
        if question["course_id"] != course_id:
            return {"ok": False, "code": 4003, "message": "无权限：该题目不属于此课程"}

        created = sql_db.add_question_favorite(user_id, course_id, question_id)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "question_id": question_id,
            "created": created,
        }}

    @staticmethod
    def unfavorite(user_id: int, course_id: int, question_id: int) -> dict:
        """取消题目收藏"""
        deleted = sql_db.remove_question_favorite(user_id, course_id, question_id)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "question_id": question_id,
            "deleted": deleted,
        }}