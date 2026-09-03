"""
教师教学监测服务：查看自己课程下的学生学习进度（对齐「教师端数据总览」增强）

设计说明：
- 系统无「选课/班级」关系表（t_course 仅关联 teacher_id），学生与课程的关联
  仅通过 t_learning_record（学习行为）隐式存在。因此「该课程的学生」= 在该课程
  有学习记录的学生，即「已开始学习的学生」。
- 「当前学习/推荐学习」未持久化（t_learning_record.status 支持 LEARNING/RECOMMENDED
  但前端仅写入 MASTERED）。此处复用 PathRecommender 以同一算法为每个学生重算，
  得到的结果与学生端看到的一致（真实计算，非伪造）。
"""
from ..core.database import db
from ..core.sql_database import sql_db
from ..services.path_recommender import PathRecommender


class TeacherService:
    """教师教学监测"""

    @staticmethod
    def get_students_progress(course_id: int) -> dict:
        # 课程全部知识点：kp_id <-> name 映射（推荐算法按 name 计算，响应需回填 kp_id）
        node_recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN n.kp_id AS kp_id, n.name AS name",
            {"cid": course_id},
        )
        kp_id_to_name = {r["kp_id"]: r["name"] for r in node_recs}
        name_to_kp_id = {v: k for k, v in kp_id_to_name.items()}
        total_knowledge = len(node_recs)

        records = sql_db.list_records_by_course(course_id)
        by_user = {}
        for r in records:
            by_user.setdefault(r["user_id"], []).append(r)

        fav_counts = sql_db.count_favorites_by_course(course_id)

        students = []
        for user_id, rs in by_user.items():
            mastered = [r for r in rs if r["status"] == "MASTERED"]
            mastered_names = [kp_id_to_name.get(r["kp_id"], r["kp_id"]) for r in mastered]

            # 复用推荐算法重算「当前学习/推荐学习」（与学生端同一算法）
            recs = PathRecommender.recommend_next(mastered_names, course_id)
            current = recs[0] if recs else None
            recommended = []
            for r in recs[1:5]:
                name = r.get("name")
                recommended.append({
                    "kp_id": name_to_kp_id.get(name),
                    "name": name,
                    "category": r.get("category") or "",
                    "reason": r.get("reason") or "",
                })

            user = sql_db.get_user_by_id(user_id)
            username = (user or {}).get("username") or ""
            name = (user or {}).get("display_name") or username or str(user_id)

            progress = (round(min(1.0, len(mastered) / total_knowledge) * 100, 1)
                        if total_knowledge else 0.0)

            students.append({
                "student_id": user_id,
                "student_name": name,
                "username": username,
                "total_knowledge": total_knowledge,
                "mastered_count": len(mastered),
                "unmastered_count": max(0, total_knowledge - len(mastered)),
                "progress": progress,
                "current_kp_id": name_to_kp_id.get(current["name"]) if current else None,
                "current_name": current["name"] if current else None,
                "recommended": recommended,
                "favorite_count": fav_counts.get(user_id, 0),
                "record_count": len(rs),
            })

        # 默认按进度升序：进度最低的学生排最前（便于教师定位薄弱学生）
        students.sort(key=lambda s: (s["progress"], s["student_id"]))

        avg = round(sum(s["progress"] for s in students) / len(students), 1) if students else 0.0

        return {
            "course_id": course_id,
            "total_knowledge": total_knowledge,
            "student_count": len(students),
            "avg_progress": avg,
            "students": students,
        }
