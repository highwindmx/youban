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
        conn.commit()
    finally:
        conn.close()


def create_conversation(conversation_id: str, title: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conversations(id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (conversation_id, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: Optional[list] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, tool_calls, created_at) VALUES(?,?,?,?,?)",
            (
                conversation_id,
                role,
                content,
                json.dumps(tool_calls) if tool_calls else None,
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
            "SELECT role, content, tool_calls FROM messages "
            "WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        out = []
        for r in reversed(rows):
            item = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                item["tool_calls"] = json.loads(r["tool_calls"])
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
            "COALESCE(c.usage_total, 0) AS usage_total, "
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
                }
            )
        return out
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
