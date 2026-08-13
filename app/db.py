"""SQLite 持久化：对话与消息历史。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import config


def _get_conn() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # 迁移：为已存在的库补上累计 token 列
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)")]
        if "usage_total" not in cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN usage_total INTEGER DEFAULT 0"
            )
        # 迁移：补上会话级工作目录范围列（按会话持久化，打开历史对话时回显）
        if "work_dir" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN work_dir TEXT")
        # 迁移：补上会话级配置列（模型 / 人设），供对话设置面板持久化
        if "model" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN model TEXT")
        if "persona" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN persona TEXT")
        # 迁移：补上会话级禁用工具列（工具权限开关），存 JSON 列表
        if "disabled_tools" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN disabled_tools TEXT")
        # 迁移：补上「实时展示思考过程」开关（仅影响深度思考模型的思考流显示，默认开启）
        if "show_reasoning" not in cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN show_reasoning INTEGER DEFAULT 1"
            )
        # 迁移：为 messages 补上 reasoning 列（深度思考模型的思考过程，供回放/导出）
        msg_cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
        if "reasoning" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT")
        conn.commit()
    finally:
        conn.close()


def create_conversation(
    conversation_id: str,
    title: str = "",
    work_dir: Optional[str] = None,
    model: Optional[str] = None,
    persona: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conversations(id, title, created_at, updated_at, work_dir, model, persona) "
            "VALUES(?,?,?,?,?,?,?)",
            (conversation_id, title, now, now, work_dir, model, persona),
        )
        conn.commit()
    finally:
        conn.close()


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: Optional[list] = None,
    reasoning: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, tool_calls, reasoning, created_at) VALUES(?,?,?,?,?,?)",
            (
                conversation_id,
                role,
                content,
                json.dumps(tool_calls) if tool_calls else None,
                reasoning,
                now,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_history(conversation_id: str, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, tool_calls, reasoning FROM messages "
            "WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        out = []
        for r in reversed(rows):
            item = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                item["tool_calls"] = json.loads(r["tool_calls"])
            if r["reasoning"]:
                item["reasoning"] = r["reasoning"]
            out.append(item)
        return out
    finally:
        conn.close()


def list_conversations(limit: int = 100) -> list[dict]:
    """返回会话列表（含首条消息作为标题预览），按更新时间倒序。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "COALESCE(c.usage_total, 0) AS usage_total, c.work_dir, c.model, c.persona, "
            "(SELECT content FROM messages m WHERE m.conversation_id=c.id "
            "ORDER BY m.id ASC LIMIT 1) AS first_msg "
            "FROM conversations c ORDER BY c.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            title = r["title"] or (r["first_msg"] or "新对话")[:30]
            out.append(
                {
                    "id": r["id"],
                    "title": title,
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "usage_total": r["usage_total"],
                    "work_dir": r["work_dir"] or "",
                    "model": r["model"] or "",
                    "persona": r["persona"] or "",
                }
            )
        return out
    finally:
        conn.close()


def set_conversation_work_dir(conversation_id: str, work_dir: Optional[str]) -> None:
    """设置/清除会话级工作目录范围（来源：前端 pick 或 chat 请求）。"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE conversations SET work_dir=? WHERE id=?",
            (work_dir or None, conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_work_dir(conversation_id: str) -> Optional[str]:
    """读取会话级工作目录范围（打开历史对话时回显）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT work_dir FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        return row["work_dir"] if row and row["work_dir"] else None
    finally:
        conn.close()


def get_conversation_config(conversation_id: str) -> dict:
    """读取会话级配置（工作目录 / 模型 / 人设 / 禁用工具 / 思考流显示），打开历史对话或设置面板时回显。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT work_dir, model, persona, disabled_tools, show_reasoning FROM conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return {
                "work_dir": "", "model": "", "persona": "",
                "disabled_tools": [], "show_reasoning": True,
            }
        disabled = row["disabled_tools"]
        if disabled:
            try:
                disabled = json.loads(disabled)
            except (json.JSONDecodeError, TypeError):
                disabled = []
        else:
            disabled = []
        # 思考流显示开关：NULL/0 视为关闭，其余（含默认 1）视为开启
        show_reasoning = row["show_reasoning"] not in (0, "0", None)
        return {
            "work_dir": row["work_dir"] or "",
            "model": row["model"] or "",
            "persona": row["persona"] or "",
            "disabled_tools": disabled,
            "show_reasoning": show_reasoning,
        }
    finally:
        conn.close()


def set_conversation_config(
    conversation_id: str,
    work_dir: Optional[str] = None,
    model: Optional[str] = None,
    persona: Optional[str] = None,
    disabled_tools: Optional[list] = None,
    show_reasoning: Optional[bool] = None,
) -> None:
    """设置会话级配置；空字符串视为清除（回退到全局默认）。

    disabled_tools 为被禁用工具名列表，空列表/None 表示全部启用（存 NULL）。
    show_reasoning 为是否实时展示深度思考模型的思考过程；None 时回退默认开启。
    """
    conn = _get_conn()
    try:
        dt = json.dumps(disabled_tools) if disabled_tools else None
        sr_val = 1 if show_reasoning is None else (1 if show_reasoning else 0)
        conn.execute(
            "UPDATE conversations SET work_dir=?, model=?, persona=?, disabled_tools=?, show_reasoning=? WHERE id=?",
            (work_dir or None, model or None, persona or None, dt, sr_val, conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def rename_conversation(conversation_id: str, title: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE conversations SET title=? WHERE id=?",
            (title[:60], conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_conversation(conversation_id: str) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        conn.commit()
    finally:
        conn.close()


def add_usage(conversation_id: str, tokens: int) -> None:
    """累加会话的 token 消耗（用于成本统计）。"""
    if not tokens:
        return
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE conversations SET usage_total = COALESCE(usage_total,0) + ? WHERE id=?",
            (tokens, conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def search_messages(query: str, limit: int = 50) -> list[dict]:
    """跨会话全文搜索消息内容，返回命中会话及匹配片段（按时间倒序）。"""
    q = f"%{query}%"
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT m.conversation_id, c.title, m.role, m.content "
            "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE m.content LIKE ? ORDER BY m.id DESC LIMIT ?",
            (q, limit),
        ).fetchall()
        out = []
        for r in rows:
            snippet = (r["content"] or "").replace("\n", " ")[:160]
            out.append(
                {
                    "conversation_id": r["conversation_id"],
                    "title": r["title"] or "新对话",
                    "role": r["role"],
                    "snippet": snippet,
                }
            )
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 跨会话长期记忆（用户画像 / 偏好 / 项目约定）
# ---------------------------------------------------------------------------
def get_all_memory() -> list[dict]:
    """返回全部长期记忆条目（key/value 列表）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT key, value FROM memory ORDER BY updated_at ASC"
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]
    finally:
        conn.close()


def upsert_memory(key: str, value: str) -> None:
    """写入/覆盖一条长期记忆（key 唯一）。"""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO memory(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now),
        )
        conn.commit()
    finally:
        conn.close()


def delete_memory(key: str) -> None:
    """删除一条长期记忆（按 key）。"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM memory WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()
