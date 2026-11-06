"""课程 CRUD、加课码与可访问范围。"""

from ..connection import _now

class CoursesMixin:
    """课程 CRUD、加课码与可访问范围。"""


    # ---------- 课程 ----------

    def create_course(self, course_name: str, teacher_id: int,
                      course_code: str = None, description: str = None,
                      join_code: str = None, join_mode: str = "approval",
                      organization: str = None, category: str = None,
                      cover: str = None, is_public: int = 1) -> int:
        """新建课程（课程中心改造新增 join_code / join_mode / 组织 / 分类 / 封面 / 是否公开）。

        前 4 个参数保持原有位置与默认值不变，历史调用方无需改动。
        """
        return self._execute(
            "INSERT INTO t_course "
            "(course_name, teacher_id, course_code, description, join_code, join_mode, "
            " organization, category, cover, is_public) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (course_name, teacher_id, course_code, description, join_code, join_mode,
             organization, category, cover, is_public),
        )


    def get_course(self, course_id: int) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_id = ?", (course_id,))


    def list_courses(self) -> list:
        return self._query("SELECT * FROM t_course ORDER BY course_id")


    def get_course_by_name(self, course_name: str) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_name = ?", (course_name,))


    def get_course_by_code(self, course_code: str) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_code = ?", (course_code,))


    def get_course_by_join_code(self, join_code: str) -> dict:
        """按加课码查课程（走 uq_course_join_code 唯一索引）"""
        return self._query_one("SELECT * FROM t_course WHERE join_code = ?", (join_code,))


    def list_courses_page(self, page: int = 1, page_size: int = 10,
                          teacher_id: int = None, keyword: str = None,
                          category: str = None, is_public: int = None,
                          join_mode: str = None,
                          course_ids: list = None,
                          exclude_course_ids: list = None):
        """分页查询课程（LEFT JOIN 教师表取教师名），返回 (total, rows)。

        课程中心改造新增过滤条件：
        - course_ids：白名单（权限范围，见 Permissions.allowed_course_ids）
        - exclude_course_ids：排除已加入/已申请的课程（发现课程页）
        - category / is_public / join_mode：发现课程页的筛选
        空列表语义：course_ids=[] 表示「无可见课程」直接返回空，绝不生成 IN ()；
        exclude_course_ids=[] 等价于不过滤。
        """
        if course_ids is not None and len(course_ids) == 0:
            return 0, []

        where, params = [], []
        if teacher_id is not None:
            where.append("c.teacher_id = ?")
            params.append(teacher_id)
        if keyword:
            where.append("(c.course_name LIKE ? OR c.description LIKE ?)")
            params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")
        if category:
            where.append("c.category = ?")
            params.append(category)
        if is_public is not None:
            where.append("c.is_public = ?")
            params.append(is_public)
        if join_mode:
            where.append("c.join_mode = ?")
            params.append(join_mode)
        if course_ids:
            where.append(f"c.course_id IN ({','.join('?' * len(course_ids))})")
            params.extend(course_ids)
        if exclude_course_ids:
            where.append(f"c.course_id NOT IN ({','.join('?' * len(exclude_course_ids))})")
            params.extend(exclude_course_ids)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        total = self._query_one(
            f"SELECT count(*) AS cnt FROM t_course c {where_sql}", tuple(params)
        )["cnt"]

        rows = self._query(
            f"""
            SELECT c.*, COALESCE(u.display_name, u.username, '') AS teacher_name
            FROM t_course c LEFT JOIN t_user u ON c.teacher_id = u.user_id
            {where_sql}
            ORDER BY c.course_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows


    def update_course(self, course_id: int, **fields) -> None:
        """更新课程字段（白名单，None 跳过表示不修改），自动刷新 updated_at。

        join_code 也在白名单内，但仅由 CourseService.refresh_join_code 调用
        （刷新即覆盖旧码）；普通课程设置接口不允许手填加课码。
        """
        allowed = {"course_name", "course_code", "description", "status",
                   "join_code", "join_mode", "organization", "category",
                   "cover", "is_public"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed or val is None:
                continue
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(course_id)
        self._execute(f"UPDATE t_course SET {', '.join(sets)} WHERE course_id = ?", tuple(params))


    def delete_course(self, course_id: int) -> int:
        """删除课程及其文档、学习记录、收藏、向量、题库（按子表->父表顺序满足外键），返回删除的文档数。

        Phase 9（技术债修复）：补充清理 t_kp_embedding——该表无外键、不参与级联，
        历史实现整课删除会残留孤儿向量，故在此显式删除。
        题库（题目/答题记录/题目收藏）同样无级联，必须在删 t_course 前显式清理，
        否则会留下指向已删课程的孤儿题目。
        Scope C 的 t_question_embedding（题目向量）与 t_kp_text（知识点文本缓存）同理。
        知识融合的 5 张表（t_kp_fusion_*）同理——它们此前只挂在文档级删除路径上，
        课程级删除会静默残留孤儿融合数据。
        """
        doc_count = self.count_documents_by_course(course_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM t_kp_embedding WHERE course_id = ?", (course_id,))
            # Scope C：题目向量与知识点文本缓存同样无外键，需显式清理（否则留孤儿）
            # 按 course_id **直删**（而非「题号子查询」）：题号子查询漏掉孤儿行
            # （题目已被删、向量还在），而向量行自带 course_id，按它删能顺带清掉孤儿。
            conn.execute("DELETE FROM t_question_embedding WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_question_import_batch WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_kp_text WHERE course_id = ?", (course_id,))
            # 知识融合：5 张表无外键，按 course_id 直删；传入 conn 以并入本事务，
            # 与「先删融合行、再删 t_course」的顺序要求一起原子生效。
            self.delete_fusion_by_course(course_id, conn=conn)
            conn.execute("DELETE FROM t_student_favorite WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_learning_record WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course_invite WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course_member WHERE course_id = ?", (course_id,))
            # 题库：先删题目收藏与答题记录（引用 t_question），再删题目本身
            conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute(
                "DELETE FROM t_answer_record WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            # L2：知识点关联（t_question_kp）有物理外键指向 t_question，必须先删
            conn.execute(
                "DELETE FROM t_question_kp WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute("DELETE FROM t_question WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_document WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course WHERE course_id = ?", (course_id,))
            conn.commit()
        return doc_count


    def update_course_join_code(self, course_id: int, join_code: str) -> int:
        """刷新加课码：单条 UPDATE 直接覆盖，旧码立即失效；返回影响行数"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_course SET join_code = ?, updated_at = ? WHERE course_id = ?",
                (join_code, _now(), course_id),
            )
            conn.commit()
            return cur.rowcount


    def list_accessible_course_ids(self, user_id: int, role: str) -> list:
        """该用户可读的课程 id 列表：教师=自己创建的 + 被邀请协作的；学生=已通过审核的成员课程。

        供 Permissions 计算「未指定课程时」的读作用域，以及数据总览/问答的范围收敛。
        """
        if role == "teacher":
            rows = self._query(
                "SELECT course_id FROM t_course WHERE teacher_id = ? "
                "UNION "
                "SELECT course_id FROM t_course_member "
                "WHERE user_id = ? AND status = 'approved'",
                (user_id, user_id),
            )
        else:
            rows = self._query(
                "SELECT course_id FROM t_course_member "
                "WHERE user_id = ? AND status = 'approved'",
                (user_id,),
            )
        return [r["course_id"] for r in rows]


    # ---------- 统计（数据总览） ----------

    def count_courses(self) -> int:
        """课程总数"""
        return self._query_one("SELECT count(*) AS cnt FROM t_course")["cnt"]
