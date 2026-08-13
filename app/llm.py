"""DeepSeek 接入 + 多轮 agent loop。

采用 OpenAI 兼容接口：发送消息 + tools，模型可返回 tool_calls；
本地执行工具后将结果回填，再次请求模型，直到无工具调用或达到迭代上限。
过程中的事件通过 async generator 流式回传给前端（SSE）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.config import config
from app import skills
from app import db

SYSTEM_PROMPT = (
    "你是一个名为 友伴 的本地 AI 助手，运行在用户的本机工作区内。"
    "你可以与用户对话，并在需要时使用可用工具来读取文件、写入文件、列出目录、"
    "搜索内容、执行命令或运行代码，从而真正替用户完成工作。"
    "请优先使用工具获取真实信息，而不是凭空猜测文件内容或命令结果。"
    "每次回复尽量简洁、面向结果。所有文件操作都限定在工作区内。"
)

# 运行时注入的环境上下文（含操作系统信息，避免模型误用 Linux 命令）
ENV_CONTEXT = (
    f"\n\n[运行环境]\n"
    f"- 操作系统：{config.OS_NAME}（{'Windows' if config.IS_WINDOWS else '非 Windows'}）\n"
    f"- 工作区根目录(WORKSPACE_ROOT)：{config.WORKSPACE_ROOT}\n"
    f"- 可操作目录(TARGET_DIR)：{config.TARGET_DIR}\n"
    f"- 文档目录(DOC_ROOT)：{config.DOC_ROOT}\n"
    f"- 命令规范：本机为 {config.OS_NAME}，执行命令或写脚本时请使用对应系统的命令"
    f"（Windows 用 dir/type/del/copy/move 等，不要用 ls/cat/rm 等 Unix 命令；"
    f"优先用专用工具如 list_dir/read_file/write_file/manage_dir，而非裸 shell 命令）。"
)

def _event(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _create_with_retry(client, **kwargs):
    """DeepSeek 调用加重试：网络抖动/限流时指数退避，最多 3 次。"""
    last_err = None
    for attempt in range(3):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1.5 ** attempt)
    raise last_err


# ---------------------------------------------------------------------------
# 上下文/历史相关的辅助函数
# ---------------------------------------------------------------------------

# 上下文 token 预算：DeepSeek 上下文约 64k，留足余量给模型输出与系统提示
BUDGET_TOKENS = 48000


def _approx_tokens(text: str) -> int:
    """粗略 token 估算：CJK/全角字符≈1 token，其余≈0.25 token。

    仅用于上下文预算裁剪，不要求精确。
    """
    if not text:
        return 0
    n = 0
    for ch in text:
        if (
            "\u4e00" <= ch <= "\u9fff"
            or "\u3000" <= ch <= "\u303f"
            or "\uff00" <= ch <= "\uffef"
        ):
            n += 1
        else:
            n += 0.25
    return int(n) + 1


def _expand_history(history: list[dict]) -> list[dict]:
    """把落库的历史消息还原成 OpenAI 格式。

    关键点：上一轮「助手调用工具 + 工具结果」被合并存在一条
    assistant(tool_calls) 记录里，这里要拆回
    assistant(tool_calls) -> tool(result) 的配对序列，否则模型会失忆。
    """
    out: list[dict] = []
    for h in history:
        role = h.get("role")
        if role == "user":
            out.append({"role": "user", "content": h.get("content", "")})
        elif role == "assistant" and h.get("tool_calls"):
            tcs = h["tool_calls"]
            out.append(
                {
                    "role": "assistant",
                    "content": h.get("content") or "",
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {
                                "name": c["function"]["name"],
                                "arguments": c["function"]["arguments"],
                            },
                        }
                        for c in tcs
                    ],
                }
            )
            for c in tcs:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": c["id"],
                        "name": c["function"]["name"],
                        "content": c.get("result") or "",
                    }
                )
        else:
            out.append({"role": "assistant", "content": h.get("content", "")})
    return out


def _record_tool_calls(tool_calls, results: list[str]) -> list[dict]:
    """把本轮工具调用 + 结果序列化，落库供回放与下一轮上下文复用。"""
    recs = []
    for tc, res in zip(tool_calls, results):
        recs.append(
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
                "result": res,
            }
        )
    return recs


def _trim_to_budget(messages: list[dict]) -> list[dict]:
    """超长对话按 token 预算从最老处滑动截断。

    始终保留系统提示；删除最老消息，但若要删的是 tool 消息，
    连同其前置的 assistant(tool_calls) 一起删，避免破坏配对；
    始终保留最后两条（当前用户消息 + 上一轮助手）。
    """
    if not messages:
        return messages
    sys_msg = messages[0]
    rest = messages[1:]
    total = _approx_tokens(sys_msg.get("content", ""))
    for m in rest:
        total += _approx_tokens(m.get("content", ""))
        for tc in m.get("tool_calls", []) or []:
            total += _approx_tokens(tc.get("function", {}).get("arguments", ""))
    if total <= BUDGET_TOKENS:
        return messages

    idx = 0
    while total > BUDGET_TOKENS and len(rest) - idx > 2:
        m = rest[idx]
        drop = [m]
        # 若删除的是 tool 消息，把它的 assistant(tool_calls) 及紧跟的所有 tool 消息一并删，
        # 避免留下「孤儿」tool 消息（OpenAI 要求 tool 必须紧跟其 assistant）。
        if m.get("role") == "tool" and idx - 1 >= 0:
            prev = rest[idx - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                drop = [prev]
                j = idx
                while j < len(rest) and rest[j].get("role") == "tool":
                    drop.append(rest[j])
                    j += 1
        for d in drop:
            total -= _approx_tokens(d.get("content", ""))
            for tc in d.get("tool_calls", []) or []:
                total -= _approx_tokens(tc.get("function", {}).get("arguments", ""))
            if d in rest:
                rest.remove(d)
        # 重新从头扫描（消息数不大，O(n^2) 可接受且稳妥）
        idx = 0
        if len(rest) <= 2:
            break
    return [sys_msg] + rest


async def chat_stream(
    user_message: str,
    conversation_id: str,
    history: list[dict],
    stop_event: asyncio.Event | None = None,
    images: list[str] | None = None,
    work_dir: str | None = None,
) -> AsyncGenerator[str, None]:
    """驱动一次完整对话，流式产出 SSE 事件。

    stop_event 被 set 时立即中止（用于前端「停止」按钮）。
    work_dir 为本次对话指定的「工作目录范围」，非空时覆盖全局根目录。
    """

    async def _should_stop() -> bool:
        if stop_event is None:
            return False
        return stop_event.is_set()

    if not config.DEEPSEEK_API_KEY:
        yield _event(
            "error",
            {"message": "未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启服务。"},
        )
        return

    # ---- 校验并设定会话级工作目录范围 ----
    if work_dir:
        try:
            p = Path(work_dir)
            p = p if p.is_absolute() else (config.WORKSPACE_ROOT / p)
            p = p.resolve()
            if not p.is_dir():
                yield _event("warning", {"message": f"指定的工作目录不存在，已忽略：{work_dir}"})
                work_dir = None
            elif config.MB_SANDBOX and config.WORKSPACE_ROOT not in p.parents and p != config.WORKSPACE_ROOT:
                yield _event("warning", {"message": f"指定的工作目录越出沙箱范围，已忽略：{work_dir}"})
                work_dir = None
            else:
                work_dir = str(p)
        except Exception:  # noqa: BLE001
            work_dir = None
    skills.set_work_dir(work_dir)
    try:
        async for chunk in _chat_inner(
            user_message, conversation_id, history, stop_event, images, work_dir
        ):
            yield chunk
    finally:
        skills.set_work_dir(None)


async def _chat_inner(
    user_message: str,
    conversation_id: str,
    history: list[dict],
    stop_event: asyncio.Event | None,
    images: list[str] | None,
    work_dir: str | None,
) -> AsyncGenerator[str, None]:
    client = AsyncOpenAI(
        api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL
    )
    # 含图片时若配置了视觉模型则切换；否则沿用默认模型（图片会被忽略，前端会提示）
    model = config.DEEPSEEK_MODEL
    # 注入跨会话长期记忆（用户偏好/项目约定/个人信息等）
    memory = db.get_all_memory()
    mem_text = ""
    if memory:
        lines = "\n".join(f"- {m['key']}: {m['value']}" for m in memory)
        mem_text = (
            "\n\n[长期记忆]\n" + lines +
            "\n（这是跨会话长期记住的用户信息/偏好/约定；如有更新请调用 remember/forget 工具。）"
        )
    # 会话级工作目录范围（如设定，则文件操作以它为根）
    wd_text = ""
    if work_dir:
        wd_text = (
            f"\n\n[本次对话工作目录范围]\n"
            f"- 工作目录(work_dir)：{work_dir}\n"
            f"- 本次对话涉及文件读写、列目录、搜索、解析文档、目录管理时，"
            f"默认以此目录为根；相对路径以此目录为基准，请勿擅自操作该目录之外的路径。"
        )
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT + ENV_CONTEXT + wd_text + mem_text}
    ]
    # 历史消息：还原 assistant(tool_calls) + tool 配对，并回喂上一轮工具结果
    messages.extend(_expand_history(history))
    if images and config.DEEPSEEK_VISION_MODEL:
        model = config.DEEPSEEK_VISION_MODEL
        content = [{"type": "text", "text": user_message}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        messages.append({"role": "user", "content": content})
    else:
        if images:
            yield _event("warning", {"message": "检测到图片，但未配置 DEEPSEEK_VISION_MODEL，图片不会被理解。请在 .env 设置视觉模型后重试。"})
        messages.append({"role": "user", "content": user_message})
    # 超长对话按 token 预算滑动截断（保留系统提示与最近若干轮）
    messages = _trim_to_budget(messages)

    for iteration in range(config.MAX_AGENT_ITERATIONS):
        if await _should_stop():
            yield _event(
                "stopped",
                {"message": "\n\n[已停止生成。]", "conversation_id": conversation_id},
            )
            return

        try:
            resp = await _create_with_retry(
                client,
                model=model,
                messages=messages,
                tools=skills.TOOL_SCHEMAS,  # type: ignore[arg-type]
                tool_choice="auto",
                stream=False,
                temperature=0.3,
            )
        except Exception as e:  # noqa: BLE001
            yield _event("error", {"message": f"调用 DeepSeek 失败: {e}"})
            return
        # 回传本次 token 用量，供累计统计（前端展示 / 落库）
        if getattr(resp, "usage", None):
            u = resp.usage
            yield _event("usage", {
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
            })

        choice = resp.choices[0].message
        tool_calls = choice.tool_calls or []

        if not tool_calls:
            # 模型给出最终文本回复
            final = choice.content or ""
            yield _event("token", {"text": final})
            yield _event("done", {"conversation_id": conversation_id})
            return

        # 把 assistant 的工具调用意图加入上下文，并逐个执行
        assistant_msg: dict = {"role": "assistant", "content": choice.content or ""}
        serialized = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
        assistant_msg["tool_calls"] = serialized
        messages.append(assistant_msg)

        tool_results: list[str] = []
        for tc in tool_calls:
            if await _should_stop():
                yield _event(
                    "stopped",
                    {"message": "\n\n[已停止生成。]", "conversation_id": conversation_id},
                )
                return
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            yield _event("tool", {"name": name, "args": args})
            result = await skills.execute_tool(name, args)
            yield _event("tool_result", {"name": name, "result": result[:2000]})
            tool_results.append(result[:4000])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": result[:4000],
                }
            )

        # 把本轮「助手调用工具 + 工具结果」整体落库，供回放与下一轮上下文复用
        db.add_message(
            conversation_id,
            "assistant",
            choice.content or "",
            tool_calls=_record_tool_calls(tool_calls, tool_results),
        )

    # 达到迭代上限，强制收尾
    yield _event(
        "token",
        {
            "text": "\n\n[已达到最大操作步数，停止自动执行。你可以继续让我处理。]"
        },
    )
    yield _event("done", {"conversation_id": conversation_id})
