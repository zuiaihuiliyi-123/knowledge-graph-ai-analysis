"""
Git 仓库历史分析服务：解析 git log + LLM 语义分析
"""
import os
import re
import json
import subprocess
import hashlib
from typing import List, Dict, Optional
from datetime import datetime

from openai import OpenAI
from ..core.config import settings


# ============================================================
# LLM Prompt 模板
# ============================================================

COMMIT_ANALYSIS_PROMPT = """你是一个代码审查和版本管理专家。请分析以下 git commit：

## Commit 信息
- Hash: {hash}
- 作者: {author}
- 日期: {date}
- 消息: {message}

## 变更文件列表
{changed_files}

## 变更统计
{stat_summary}

## 要求
请分析并返回严格JSON格式（不要包含markdown代码块标记）：
{{
  "intent": "这次提交的意图（用中文一句话概括）",
  "category": "feature|fix|refactor|docs|test|other",
  "affected_modules": ["受影响的模块或目录名"],
  "risk_level": "low|medium|high",
  "introduces": ["引入的新功能或特性（没有则为空数组）"],
  "fixes": ["修复的问题或bug（没有则为空数组）"],
  "breaking_change": true/false,
  "summary": "对这次变更的简要总结（2-3句话）"
}}"""


RESTORE_IMPACT_PROMPT = """你是一个版本管理和风险分析专家。用户计划将代码仓库从当前版本恢复到目标版本。

## 当前版本
{current_version}

## 目标版本（要恢复到的版本）
{target_version}

## 将会被回滚的提交列表（从新到旧）
{commits_to_rollback}

## 这些提交总共修改的文件
{affected_files}

## 要求
分析恢复到目标版本的影响，返回严格JSON格式（不要包含markdown代码块标记）：
{{
  "is_safe": true/false,
  "risk_level": "low|medium|high|critical",
  "will_lose_features": ["会被丢失的功能"],
  "will_lose_fixes": ["会被回滚的修复"],
  "conflict_risk_files": ["可能产生冲突的文件"],
  "recommended_approach": "建议的操作方式：reset（硬回滚）或 revert（安全回滚）",
  "commands": ["建议执行的git命令序列"],
  "warning": "需要注意的风险警告（没有则为空字符串）",
  "summary": "整体影响评估（2-3句话）"
}}"""


# ============================================================
# Git 命令封装
# ============================================================

class GitRepoAnalyzer:
    """Git 仓库分析器 —— 纯 subprocess 实现，零额外依赖"""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self._validate_git_repo()

    def _run_git(self, args: List[str], timeout: int = 30) -> str:
        """执行 git 命令并返回 stdout"""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git error: {result.stderr.strip()}")
            return result.stdout
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Git 命令超时: git {' '.join(args)}")

    def _validate_git_repo(self):
        """验证是否为有效 git 仓库"""
        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            raise ValueError(f"不是有效的 git 仓库: {self.repo_path}")
        self._run_git(["rev-parse", "--git-dir"])  # 二次确认

    @property
    def repo_id(self) -> str:
        """生成仓库唯一标识（路径的 MD5 前12位）"""
        return hashlib.md5(self.repo_path.encode()).hexdigest()[:12]

    @property
    def repo_name(self) -> str:
        """仓库名称（目录名）"""
        return os.path.basename(self.repo_path)

    # ---- 获取 commit 列表 ----

    def get_commits(
        self,
        max_count: int = 50,
        branch: str = None,
        skip_merges: bool = True,
    ) -> List[dict]:
        """
        获取 commit 列表（结构化）
        返回: [{hash, author, email, date, message, refs, ...}, ...]
        """
        args = [
            "log",
            f"--max-count={max_count}",
            "--format=%H|%an|%ae|%ad|%s",
            "--date=iso-strict",
            "--all",
            "--decorate=short",
        ]
        if skip_merges:
            args.append("--no-merges")

        if branch:
            args.append(branch)

        output = self._run_git(args)

        commits = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue

            hash_val, author, email, date_str, rest = parts

            # 解析 refs（分支/标签装饰）
            refs = []
            message = rest
            ref_match = re.match(r"^\((.+?)\)\s*(.*)", rest)
            if ref_match:
                refs = [r.strip() for r in ref_match.group(1).split(",")]
                message = ref_match.group(2)

            # 过滤掉远程分支装饰: origin/HEAD, origin/main 等
            # 提取本地有用的标签信息
            tags = [r for r in refs if "tag:" in r]
            branches = [r for r in refs if not r.startswith("tag:")]

            commits.append({
                "hash": hash_val,
                "short_hash": hash_val[:7],
                "author": author,
                "email": email,
                "date": date_str,
                "message": message.strip(),
                "tags": tags,
                "branches": branches,
            })

        return commits

    # ---- 获取单个 commit 的详细信息 ----

    def get_commit_detail(self, commit_hash: str) -> dict:
        """获取单个 commit 的变更文件列表和统计"""
        # 获取变更文件列表
        files_output = self._run_git([
            "diff-tree", "--no-commit-id", "-r",
            "--name-status", commit_hash,
        ])

        changed_files = []
        for line in files_output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                status, filepath = parts
                changed_files.append({
                    "status": status.strip(),  # A=Added, M=Modified, D=Deleted, R=Renamed
                    "file": filepath.strip(),
                })

        # 获取统计信息
        stat_output = self._run_git([
            "show", "--stat", "--format=", commit_hash,
        ])
        # 提取最后一行（变更统计摘要）
        stat_lines = stat_output.strip().split("\n")
        stat_summary = ""
        for line in reversed(stat_lines):
            if "changed" in line and "insertion" in line or "deletion" in line:
                stat_summary = line.strip()
                break
        if not stat_summary and stat_lines:
            # 取最后非空行
            for line in reversed(stat_lines):
                if line.strip():
                    stat_summary = line.strip()
                    break

        return {
            "hash": commit_hash,
            "short_hash": commit_hash[:7],
            "changed_files": changed_files,
            "file_count": len(changed_files),
            "stat_summary": stat_summary,
        }

    # ---- 获取 diff ----

    def get_diff_summary(self, commit_hash: str, max_diff_lines: int = 200) -> str:
        """获取 commit 的 diff 摘要（截断到 max_diff_lines 行）"""
        diff = self._run_git([
            "show", "--format=", "--no-color", commit_hash,
        ], timeout=60)

        lines = diff.split("\n")
        if len(lines) > max_diff_lines:
            # 保留开头，展示被截断的行数
            truncated_lines = lines[:max_diff_lines]
            truncated_lines.append(
                f"\n... [diff truncated: {len(lines) - max_diff_lines} more lines]"
            )
            return "\n".join(truncated_lines)
        return diff

    # ---- 获取两个版本之间的 commits ----

    def get_commits_between(self, from_version: str, to_version: str = "HEAD") -> List[dict]:
        """
        获取 from_version..to_version 之间的 commit 列表
        from_version 不包含，to_version 包含
        """
        range_spec = f"{from_version}..{to_version}"
        output = self._run_git([
            "log",
            "--format=%H|%an|%ae|%ad|%s",
            "--date=iso-strict",
            range_spec,
            "--no-merges",
        ])

        commits = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue
            commits.append({
                "hash": parts[0],
                "short_hash": parts[0][:7],
                "author": parts[1],
                "email": parts[2],
                "date": parts[3],
                "message": parts[4].strip(),
            })
        return commits

    def get_current_head(self) -> str:
        """获取当前 HEAD 的 hash"""
        return self._run_git(["rev-parse", "HEAD"]).strip()

    def get_all_branches(self) -> List[str]:
        """获取所有分支名"""
        output = self._run_git(["branch", "-a", "--format=%(refname:short)"])
        return [b.strip() for b in output.strip().split("\n") if b.strip()]

    def get_all_tags(self) -> List[str]:
        """获取所有标签"""
        output = self._run_git(["tag", "-l"])
        return [t.strip() for t in output.strip().split("\n") if t.strip()]


# ============================================================
# LLM 驱动的 Git 分析器
# ============================================================

class GitCommitAnalyzer:
    """使用 LLM 分析 git commit 的语义信息"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

    async def analyze_commit(self, commit: dict, detail: dict, diff_summary: str) -> dict:
        """
        用 LLM 深度分析单个 commit
        返回结构化分析结果
        """
        # 格式化变更文件列表
        files_str = "\n".join(
            f"  [{f['status']}] {f['file']}"
            for f in detail.get("changed_files", [])
        )
        if not files_str:
            files_str = "(无法获取文件列表)"

        prompt = COMMIT_ANALYSIS_PROMPT.format(
            hash=commit["hash"],
            author=commit["author"],
            date=commit["date"],
            message=commit["message"],
            changed_files=files_str,
            stat_summary=detail.get("stat_summary", "(无统计)"),
        )

        # 如果有 diff，追加到 prompt
        if diff_summary and diff_summary.strip():
            prompt += f"\n\n## Diff 内容（截断）\n```diff\n{diff_summary[:3000]}\n```"

        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个精确的代码分析和版本管理助手。请只输出JSON格式。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
                timeout=settings.EXTRACTION_TIMEOUT,
            )

            content = response.choices[0].message.content.strip()
            return self._parse_json_response(content)

        except json.JSONDecodeError:
            return self._fallback_analysis(commit, detail)
        except Exception as e:
            return {"error": str(e), **self._fallback_analysis(commit, detail)}

    def _parse_json_response(self, content: str) -> dict:
        """清理并解析 LLM 返回的 JSON"""
        # 清理 markdown 代码块
        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()
        return json.loads(content)

    def _fallback_analysis(self, commit: dict, detail: dict) -> dict:
        """LLM 不可用时的降级分析（基于规则）"""
        msg_lower = commit["message"].lower()

        # 基于 commit message 关键词判断类别
        if any(kw in msg_lower for kw in ["fix", "bug", "修复", "bugfix", "hotfix"]):
            category = "fix"
        elif any(kw in msg_lower for kw in ["refactor", "重构", "clean", "清理"]):
            category = "refactor"
        elif any(kw in msg_lower for kw in ["doc", "readme", "文档", "comment"]):
            category = "docs"
        elif any(kw in msg_lower for kw in ["test", "测试", "spec"]):
            category = "test"
        elif any(kw in msg_lower for kw in ["feat", "add", "新增", "添加", "实现"]):
            category = "feature"
        else:
            category = "other"

        return {
            "intent": commit["message"],
            "category": category,
            "affected_modules": [],
            "risk_level": "low",
            "introduces": [],
            "fixes": [],
            "breaking_change": "!" in commit["message"]
            or "breaking" in msg_lower,
            "summary": commit["message"],
        }

    # ---- 版本恢复影响分析 ----

    async def analyze_restore_impact(
        self,
        current_version: str,
        target_version: str,
        commits_to_rollback: List[dict],
        affected_files: List[str],
    ) -> dict:
        """用 LLM 分析恢复到目标版本的影响"""

        # 格式化将被回滚的 commit 列表
        commits_str = "\n".join(
            f"  - {c['short_hash']}: {c['message'][:80]} (by {c['author']})"
            for c in commits_to_rollback
        )
        if not commits_str:
            commits_str = "(没有需要回滚的提交——目标版本可能晚于当前版本)"

        files_str = "\n".join(f"  - {f}" for f in affected_files[:50])
        if len(affected_files) > 50:
            files_str += f"\n  ... 及其他 {len(affected_files) - 50} 个文件"
        if not files_str:
            files_str = "(无文件变更)"

        prompt = RESTORE_IMPACT_PROMPT.format(
            current_version=current_version,
            target_version=target_version,
            commits_to_rollback=commits_str,
            affected_files=files_str,
        )

        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个版本管理和风险评估专家。请只输出JSON格式。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
                timeout=settings.QA_TIMEOUT,
            )

            content = response.choices[0].message.content.strip()
            return self._parse_json_response(content)

        except (json.JSONDecodeError, Exception) as e:
            return self._fallback_restore_impact(
                current_version, target_version, commits_to_rollback, affected_files
            )

    def _fallback_restore_impact(
        self,
        current_version: str,
        target_version: str,
        commits_to_rollback: List[dict],
        affected_files: List[str],
    ) -> dict:
        """LLM 不可用时的降级恢复分析"""
        n = len(commits_to_rollback)

        if n == 0:
            return {
                "is_safe": True,
                "risk_level": "low",
                "will_lose_features": [],
                "will_lose_fixes": [],
                "conflict_risk_files": [],
                "recommended_approach": "无需操作",
                "commands": ["# 当前版本与目标版本相同或目标版本更新，无需回滚"],
                "warning": "",
                "summary": "目标版本与当前版本之间没有需要回滚的提交。",
            }

        return {
            "is_safe": n <= 3,
            "risk_level": "low" if n <= 2 else "medium" if n <= 5 else "high",
            "will_lose_features": [],
            "will_lose_fixes": [],
            "conflict_risk_files": affected_files[:10],
            "recommended_approach": (
                "git revert（推荐）" if n <= 5 else "git reset --hard（谨慎使用）"
            ),
            "commands": [
                f"# 方案一：硬回滚（会丢失历史）",
                f"git reset --hard {target_version}",
                f"# 方案二：安全回滚（保留历史，逐个 revert）",
                *[
                    f"git revert --no-commit {c['short_hash']}"
                    for c in commits_to_rollback[:5]
                ],
                f"git commit -m 'Revert to {target_version[:7]}'",
            ],
            "warning": (
                "⚠️ 使用 git reset --hard 会永久删除提交历史，请先备份！"
                if n > 3
                else ""
            ),
            "summary": f"将回滚 {n} 个提交，影响 {len(affected_files)} 个文件。",
        }
