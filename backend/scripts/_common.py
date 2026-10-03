"""
资料生成脚本公共工具：LLM 调用、题目结构解析
供 translate_openstax_ch34.py / complete_openstax_ch34.py / assemble_openstax_ch34.py 共用，
避免三份复制粘贴（含 parse_nums 的"数值行防误判"修复，只维护一处）。
"""
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用 app 层统一的 OpenAI 客户端与配置（settings 走 python-dotenv 加载 backend/.env，
# 与全项目其他 LLM 调用方同一封装；prepare_chapter.py 已有同一 import 范式）
sys.path.insert(0, BASE)
from app.core.config import settings  # noqa: E402
from openai import OpenAI  # noqa: E402

_client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_API_BASE)


def call_llm(system: str, user: str, max_tokens: int = 4096, retries: int = 3):
    """调 DeepSeek chat/completions，返回文本内容；重试耗尽返回 None"""
    for attempt in range(retries):
        try:
            resp = _client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
                timeout=300,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            time.sleep(5)
    return None


def llm_json(system: str, user: str, max_tokens: int = 4096, retries: int = 3):
    """调 LLM 并解析 JSON（自动剥离 ```json 代码围栏）"""
    content = call_llm(system, user, max_tokens=max_tokens, retries=retries)
    if content is None:
        return None
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
    return json.loads(content)


# 题号行判定：点后是空白或行尾（(?=\s|$)），防止 "30.8 m" 这类行首数值行被误判为题号
_NUM_RE = re.compile(r'^(\d+)\.(?=\s|$)\s*(.*)$', re.M)


def parse_nums(text: str) -> dict:
    """按行首"题号."切分题目/答案文本，返回 {题号: 内容}"""
    items, cur, buf = {}, None, []
    for line in text.split('\n'):
        m = _NUM_RE.match(line)
        if m:
            if cur is not None:
                items[cur] = '\n'.join(buf).strip()
            cur = int(m.group(1))
            buf = [m.group(2).strip()] if m.group(2).strip() else []
        elif cur is not None:
            buf.append(line.strip())
    if cur is not None:
        items[cur] = '\n'.join(buf).strip()
    return items


def question_numbers(text: str) -> set:
    """只取题号集合（不构建 {题号: 全文} 字典），判定规则与 parse_nums 共用"""
    return {int(m.group(1)) for m in _NUM_RE.finditer(text)}


def split_phys_sections(q_text: str) -> tuple:
    """物理题目文本按"概念题/习题"分区，返回 (概念题文本, 习题文本)

    分区标记兼容翻译变体（概念性问题/概念题/Conceptual、习题/Problems）；
    找不到分区标记时全部归习题区。
    """
    cq_m = re.search(r'^[#\s]*(概念性问题|概念题|Conceptual)[^\n]*\n', q_text, re.M)
    pe_m = re.search(r'^[#\s]*(习题|Problems)[^\n]*\n', q_text, re.M)
    if cq_m and pe_m and pe_m.start() > cq_m.start():
        return q_text[cq_m.end():pe_m.start()], q_text[pe_m.end():]
    return '', q_text
