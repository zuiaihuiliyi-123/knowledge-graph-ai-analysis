"""
Git 知识图谱管理服务：将 Git 历史建模到 Neo4j 图数据库
"""
from typing import List, Dict, Optional
from ..core.database import db


class GitKnowledgeGraphManager:
    """Git 仓库知识图谱管理器 —— Neo4j 存储与查询"""

    # ================================================================
    # 初始化（建约束 + 索引）
    # ================================================================

    @staticmethod
    def init_schema():
        """初始化 Git 知识图谱的 Neo4j 约束和索引"""
        constraints = [
            "CREATE CONSTRAINT git_commit_hash IF NOT EXISTS FOR (c:GitCommit) REQUIRE c.hash IS UNIQUE",
            "CREATE CONSTRAINT git_file_path IF NOT EXISTS FOR (f:GitFile) REQUIRE f.path IS UNIQUE",
            "CREATE CONSTRAINT git_author_email IF NOT EXISTS FOR (a:GitAuthor) REQUIRE a.email IS UNIQUE",
        ]
        for cypher in constraints:
            try:
                db.query(cypher)
            except Exception:
                pass  # 约束已存在则忽略

    # ================================================================
    # 写入图谱
    # ================================================================

    @staticmethod
    def build_graph(
        repo_id: str,
        repo_name: str,
        repo_path: str,
        commits_analysis: List[dict],
    ) -> dict:
        """
        将分析结果写入 Neo4j
        commits_analysis: [{"commit": {...}, "detail": {...}, "analysis": {...}}, ...]
        """
        node_count = 0
        relation_count = 0

        # 先创建仓库根节点
        db.query(
            """
            MERGE (r:GitRepo {repo_id: $repo_id})
            SET r.name = $repo_name, r.path = $repo_path, r.updated_at = datetime()
            RETURN r
            """,
            {"repo_id": repo_id, "repo_name": repo_name, "repo_path": repo_path},
        )
        node_count += 1

        prev_hash = None

        for item in commits_analysis:
            commit = item["commit"]
            detail = item.get("detail", {})
            analysis = item.get("analysis", {})

            # ---- 创建 Commit 节点 ----
            db.query(
                """
                MERGE (c:GitCommit {hash: $hash})
                SET c.short_hash = $short_hash,
                    c.message = $message,
                    c.author = $author,
                    c.email = $email,
                    c.date = datetime($date),
                    c.category = $category,
                    c.intent = $intent,
                    c.risk_level = $risk_level,
                    c.breaking_change = $breaking_change,
                    c.summary = $summary,
                    c.repo_id = $repo_id
                RETURN c
                """,
                {
                    "hash": commit["hash"],
                    "short_hash": commit["short_hash"],
                    "message": commit["message"],
                    "author": commit["author"],
                    "email": commit["email"],
                    "date": commit["date"],
                    "category": analysis.get("category", "other"),
                    "intent": analysis.get("intent", commit["message"]),
                    "risk_level": analysis.get("risk_level", "low"),
                    "breaking_change": analysis.get("breaking_change", False),
                    "summary": analysis.get("summary", ""),
                    "repo_id": repo_id,
                },
            )
            node_count += 1

            # ---- 创建 Author 节点 + AUTHORED_BY 关系 ----
            db.query(
                """
                MERGE (a:GitAuthor {email: $email})
                SET a.name = $name
                WITH a
                MATCH (c:GitCommit {hash: $hash})
                MERGE (c)-[:AUTHORED_BY]->(a)
                """,
                {"email": commit["email"], "name": commit["author"], "hash": commit["hash"]},
            )
            node_count += 1
            relation_count += 1

            # ---- Commit → 仓库关系 ----
            db.query(
                """
                MATCH (c:GitCommit {hash: $hash})
                MATCH (r:GitRepo {repo_id: $repo_id})
                MERGE (c)-[:BELONGS_TO]->(r)
                """,
                {"hash": commit["hash"], "repo_id": repo_id},
            )
            relation_count += 1

            # ---- Commit → Commit 父子关系 ----
            if prev_hash:
                db.query(
                    """
                    MATCH (parent:GitCommit {hash: $parent})
                    MATCH (child:GitCommit {hash: $child})
                    MERGE (parent)-[:PARENT_OF]->(child)
                    """,
                    {"parent": prev_hash, "child": commit["hash"]},
                )
                relation_count += 1
            prev_hash = commit["hash"]

            # ---- 文件节点 + MODIFIED 关系 ----
            for f in detail.get("changed_files", []):
                db.query(
                    """
                    MERGE (gf:GitFile {path: $path})
                    SET gf.repo_id = $repo_id
                    WITH gf
                    MATCH (c:GitCommit {hash: $hash})
                    MERGE (c)-[:MODIFIED {status: $status}]->(gf)
                    """,
                    {
                        "path": f["file"],
                        "repo_id": repo_id,
                        "hash": commit["hash"],
                        "status": f["status"],
                    },
                )
                node_count += 1
                relation_count += 1

            # ---- 分支关系 ----
            for branch_ref in commit.get("tags", []):
                branch_name = branch_ref.replace("tag:", "").strip()
                if branch_name:
                    db.query(
                        """
                        MERGE (t:GitTag {name: $name, repo_id: $repo_id})
                        WITH t
                        MATCH (c:GitCommit {hash: $hash})
                        MERGE (c)-[:TAGGED_AS]->(t)
                        """,
                        {"name": branch_name, "repo_id": repo_id, "hash": commit["hash"]},
                    )
                    node_count += 1
                    relation_count += 1

            # ---- 引入的功能节点 ----
            for feature in analysis.get("introduces", []):
                if feature:
                    db.query(
                        """
                        MERGE (f:GitFeature {name: $name, repo_id: $repo_id})
                        WITH f
                        MATCH (c:GitCommit {hash: $hash})
                        MERGE (c)-[:INTRODUCES]->(f)
                        """,
                        {"name": feature, "repo_id": repo_id, "hash": commit["hash"]},
                    )
                    node_count += 1
                    relation_count += 1

            # ---- 修复的问题节点 ----
            for fix in analysis.get("fixes", []):
                if fix:
                    db.query(
                        """
                        MERGE (f:GitFix {name: $name, repo_id: $repo_id})
                        WITH f
                        MATCH (c:GitCommit {hash: $hash})
                        MERGE (c)-[:FIXES]->(f)
                        """,
                        {"name": fix, "repo_id": repo_id, "hash": commit["hash"]},
                    )
                    node_count += 1
                    relation_count += 1

        return {
            "repo_id": repo_id,
            "repo_name": repo_name,
            "commits_processed": len(commits_analysis),
            "node_count": node_count,
            "relation_count": relation_count,
        }

    # ================================================================
    # 查询图谱
    # ================================================================

    @staticmethod
    def get_repo_list() -> List[dict]:
        """获取已分析的所有仓库列表"""
        records = db.query(
            """
            MATCH (r:GitRepo)
            OPTIONAL MATCH (c:GitCommit)-[:BELONGS_TO]->(r)
            RETURN r.repo_id AS repo_id, r.name AS name, r.path AS path,
                   r.updated_at AS updated_at, count(c) AS commit_count
            ORDER BY r.updated_at DESC
            """
        )
        return [
            {
                "repo_id": r["repo_id"],
                "name": r["name"],
                "path": r["path"],
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
                "commit_count": r["commit_count"],
            }
            for r in records
        ]

    @staticmethod
    def get_graph_data(repo_id: str) -> dict:
        """
        获取 Git 仓库的知识图谱数据，转为 ECharts 格式
        返回 {"nodes": [...], "links": [...]}
        """
        # 获取 commits 及其关系
        records = db.query(
            """
            MATCH (c:GitCommit {repo_id: $repo_id})
            OPTIONAL MATCH (c)-[r:PARENT_OF]->(child:GitCommit)
            OPTIONAL MATCH (c)-[:AUTHORED_BY]->(a:GitAuthor)
            RETURN c, r, child, a
            """,
            {"repo_id": repo_id},
        )

        nodes = {}
        links = []

        for record in records:
            c = record.get("c")
            if c:
                node_id = c.get("hash")
                if node_id not in nodes:
                    nodes[node_id] = {
                        "id": node_id,
                        "name": c.get("short_hash", node_id[:7]),
                        "label": c.get("message", "")[:50],
                        "category": c.get("category", "other"),
                        "author": c.get("author", ""),
                        "date": str(c.get("date", "")),
                        "risk_level": c.get("risk_level", "low"),
                        "symbolSize": _get_node_size(c.get("category")),
                        "itemStyle": _get_node_style(c.get("category")),
                        "node_type": "commit",
                    }

            child = record.get("child")
            if child:
                child_id = child.get("hash")
                if child_id not in nodes:
                    nodes[child_id] = {
                        "id": child_id,
                        "name": child.get("short_hash", child_id[:7]),
                        "label": child.get("message", "")[:50],
                        "category": child.get("category", "other"),
                        "author": child.get("author", ""),
                        "date": str(child.get("date", "")),
                        "risk_level": child.get("risk_level", "low"),
                        "symbolSize": _get_node_size(child.get("category")),
                        "itemStyle": _get_node_style(child.get("category")),
                        "node_type": "commit",
                    }

            r = record.get("r")
            if r and record.get("child"):
                links.append({
                    "source": c.get("hash"),
                    "target": child.get("hash"),
                    "type": "PARENT_OF",
                })

        # 获取文件节点
        file_records = db.query(
            """
            MATCH (c:GitCommit {repo_id: $repo_id})-[mod:MODIFIED]->(f:GitFile)
            RETURN f.path AS path, count(mod) AS change_count, c.hash AS commit_hash
            """,
            {"repo_id": repo_id},
        )

        for fr in file_records:
            path = fr["path"]
            if path and path not in nodes:
                nodes[path] = {
                    "id": path,
                    "name": path.split("/")[-1] if "/" in path else path,
                    "label": path,
                    "category": "file",
                    "change_count": fr["change_count"],
                    "symbolSize": min(20 + fr["change_count"] * 2, 50),
                    "itemStyle": {"color": "#67C23A"},
                    "node_type": "file",
                }
            if path and fr.get("commit_hash"):
                links.append({
                    "source": fr["commit_hash"],
                    "target": path,
                    "type": "MODIFIED",
                })

        # 获取标签节点
        tag_records = db.query(
            """
            MATCH (t:GitTag {repo_id: $repo_id})<-[:TAGGED_AS]-(c:GitCommit)
            RETURN t.name AS name, c.hash AS commit_hash
            """,
            {"repo_id": repo_id},
        )

        for tr in tag_records:
            tag_name = tr["name"]
            if tag_name and tag_name not in nodes:
                nodes[tag_name] = {
                    "id": tag_name,
                    "name": tag_name,
                    "label": f"🏷️ {tag_name}",
                    "category": "tag",
                    "symbolSize": 30,
                    "itemStyle": {"color": "#E6A23C"},
                    "node_type": "tag",
                }
            if tag_name and tr.get("commit_hash"):
                links.append({
                    "source": tr["commit_hash"],
                    "target": tag_name,
                    "type": "TAGGED_AS",
                })

        return {
            "nodes": list(nodes.values()),
            "links": links,
        }

    @staticmethod
    def get_commits(repo_id: str, limit: int = 50) -> List[dict]:
        """获取仓库的所有 commit 节点"""
        records = db.query(
            """
            MATCH (c:GitCommit {repo_id: $repo_id})
            RETURN c.hash AS hash, c.short_hash AS short_hash,
                   c.message AS message, c.author AS author,
                   c.date AS date, c.category AS category,
                   c.risk_level AS risk_level, c.intent AS intent,
                   c.summary AS summary
            ORDER BY c.date DESC
            LIMIT $limit
            """,
            {"repo_id": repo_id, "limit": limit},
        )
        return [
            {
                "hash": r["hash"],
                "short_hash": r["short_hash"],
                "message": r["message"],
                "author": r["author"],
                "date": str(r["date"]) if r["date"] else None,
                "category": r["category"],
                "risk_level": r["risk_level"],
                "intent": r["intent"],
                "summary": r["summary"],
            }
            for r in records
        ]

    @staticmethod
    def get_restore_path(repo_id: str, target_version: str) -> dict:
        """
        查询从当前 HEAD 恢复到目标版本的路径
        返回受影响的所有 commit 和文件
        """
        # 找到目标版本之后的全部 commit（沿 PARENT_OF 链）
        records = db.query(
            """
            MATCH (target:GitCommit {repo_id: $repo_id})
            WHERE target.hash STARTS WITH $target_version OR target.short_hash = $target_version
            MATCH path = (target)-[:PARENT_OF*]->(later:GitCommit)
            RETURN target, later, path
            ORDER BY later.date
            """,
            {"repo_id": repo_id, "target_version": target_version},
        )

        commits_affected = []
        seen = set()

        for r in records:
            later = r.get("later")
            if later:
                h = later.get("hash")
                if h and h not in seen:
                    seen.add(h)
                    commits_affected.append({
                        "hash": h,
                        "short_hash": later.get("short_hash", h[:7]),
                        "message": later.get("message", ""),
                        "author": later.get("author", ""),
                        "date": str(later.get("date", "")),
                        "category": later.get("category", "other"),
                        "risk_level": later.get("risk_level", "low"),
                    })

        # 获取这些 commit 修改的全部文件
        files_affected = []
        if seen:
            file_records = db.query(
                """
                MATCH (c:GitCommit)-[:MODIFIED]->(f:GitFile)
                WHERE c.hash IN $hashes
                RETURN DISTINCT f.path AS path
                """,
                {"hashes": list(seen)},
            )
            files_affected = [fr["path"] for fr in file_records]

        return {
            "target_version": target_version,
            "commits_to_rollback": commits_affected,
            "commit_count": len(commits_affected),
            "affected_files": files_affected,
            "file_count": len(files_affected),
        }

    @staticmethod
    def get_commit_detail_with_files(repo_id: str, commit_hash: str) -> dict:
        """获取单个 commit 及其关联的文件"""
        records = db.query(
            """
            MATCH (c:GitCommit {repo_id: $repo_id, hash: $hash})
            OPTIONAL MATCH (c)-[mod:MODIFIED]->(f:GitFile)
            OPTIONAL MATCH (c)-[:AUTHORED_BY]->(a:GitAuthor)
            RETURN c, collect(DISTINCT {status: mod.status, file: f.path}) AS files,
                   a.name AS author_name
            """,
            {"repo_id": repo_id, "hash": commit_hash},
        )

        results = []
        for r in records:
            c = r.get("c")
            if c:
                results.append({
                    "hash": c.get("hash"),
                    "short_hash": c.get("short_hash"),
                    "message": c.get("message"),
                    "author": c.get("author"),
                    "date": str(c.get("date")) if c.get("date") else None,
                    "category": c.get("category"),
                    "risk_level": c.get("risk_level"),
                    "intent": c.get("intent", ""),
                    "summary": c.get("summary", ""),
                    "breaking_change": c.get("breaking_change", False),
                    "files": [
                        {"status": f["status"], "path": f["file"]}
                        for f in r.get("files", [])
                        if f["file"]
                    ],
                })
        return results[0] if results else {}


# ================================================================
# ECharts 渲染辅助
# ================================================================

def _get_node_size(category: str) -> int:
    """根据 commit 类别返回节点大小"""
    sizes = {
        "feature": 45,
        "fix": 35,
        "refactor": 30,
        "docs": 20,
        "test": 25,
        "other": 25,
    }
    return sizes.get(category, 25)


def _get_node_style(category: str) -> dict:
    """根据 commit 类别返回节点颜色"""
    colors = {
        "feature": "#409EFF",  # 蓝色 - 新功能
        "fix": "#F56C6C",       # 红色 - 修复
        "refactor": "#E6A23C",  # 橙色 - 重构
        "docs": "#909399",      # 灰色 - 文档
        "test": "#67C23A",      # 绿色 - 测试
        "other": "#B0C4DE",     # 浅蓝灰 - 其他
    }
    return {"color": colors.get(category, "#B0C4DE")}
