"""llm.py 上下文相关函数单元测试：

- _expand_history：落库历史（助手工具调用 + 工具结果合并存一条）还原为
  assistant(tool_calls) -> tool(result) 配对序列，防止模型失忆。
- _trim_to_budget：超长对话按 token 预算从最老处滑动截断，保留系统提示、
  最近两轮，且不破坏 tool/assistant(tool_calls) 配对（不留孤儿 tool）。
- _approx_tokens：粗略 token 估算。
- 会话级 persona / model 注入：chat_stream 把人设写入系统提示、把模型覆盖全局默认。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app import llm


# ---------------- _expand_history ----------------
def test_expand_history_user():
    out = llm._expand_history([{"role": "user", "content": "hi"}])
    assert out == [{"role": "user", "content": "hi"}]


def test_expand_history_tool_calls_pairing():
    hist = [
        {"role": "user", "content": "read a"},
        {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"a"}'},
                "result": "内容",
            }],
        },
        {"role": "assistant", "content": "done"},
    ]
    out = llm._expand_history(hist)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "assistant"]
    tool_msg = out[2]
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["name"] == "read_file"
    assert tool_msg["content"] == "内容"
    # assistant(tool_calls) 必须保留 tool_calls 字段且 id 对应
    assert out[1]["tool_calls"][0]["id"] == "c1"


def test_expand_history_no_tool_calls():
    out = llm._expand_history([{"role": "assistant", "content": "hi"}])
    assert out[0]["role"] == "assistant"
    assert "tool_calls" not in out[0]


# ---------------- _approx_tokens ----------------
def test_approx_tokens_empty():
    assert llm._approx_tokens("") == 0


def test_approx_tokens_cjk():
    # 每个 CJK 字符 ≈ 1 token
    assert llm._approx_tokens("你好世界") >= 4


def test_approx_tokens_ascii():
    n = llm._approx_tokens("hello world")
    assert 1 <= n <= 5  # 0.25*11 + 1 ≈ 3


# ---------------- _trim_to_budget ----------------
def test_trim_within_budget():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    with patch.object(llm, "BUDGET_TOKENS", 100000):
        out = llm._trim_to_budget(msgs)
    assert out == msgs


def test_trim_keeps_system_and_drops_oldest():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": "a1",
            "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "x", "content": "r1"},
        {"role": "user", "content": "u2"},
    ]
    with patch.object(llm, "BUDGET_TOKENS", 5):
        out = llm._trim_to_budget(msgs)
    # 系统提示始终保留
    assert out[0]["role"] == "system"
    # 任何 tool 消息前必须紧邻带 tool_calls 的 assistant（无孤儿 tool）
    for i, m in enumerate(out):
        if m["role"] == "tool":
            assert out[i - 1]["role"] == "assistant" and out[i - 1].get("tool_calls")
    # 被丢弃的是最早的 user(u1)
    assert "u1" not in [m.get("content") for m in out if m.get("content")]


def test_trim_no_orphan_tool():
    msgs = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": "a0",
            "tool_calls": [{"id": "c0", "type": "function",
                             "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c0", "name": "x", "content": "r0"},
        {"role": "user", "content": "u_last"},
    ]
    with patch.object(llm, "BUDGET_TOKENS", 3):
        out = llm._trim_to_budget(msgs)
    for i, m in enumerate(out):
        if m["role"] == "tool":
            assert out[i - 1]["role"] == "assistant" and out[i - 1].get("tool_calls")
    assert out[0]["role"] == "system"
    assert out[-1]["role"] == "user"


# ---------------- 会话级 persona / model 注入 ----------------
def test_persona_and_model_injected():
    """chat_stream 应把 persona 写入系统提示、把 model 覆盖全局默认。"""
    captured = {}

    fake_resp = MagicMock()
    fake_resp.usage = None
    fake_choice = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = "ok"
    fake_msg.tool_calls = None
    fake_choice.message = fake_msg
    fake_resp.choices = [fake_choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch.object(llm, "AsyncOpenAI", return_value=fake_client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.db, "get_all_memory", return_value=[]):

        async def run():
            async for _ in llm.chat_stream(
                user_message="hi",
                conversation_id="c1",
                history=[],
                model="deepseek-reasoner",
                persona="你是一个严谨的 Python 代码审查员",
            ):
                pass
            kwargs = fake_client.chat.completions.create.call_args.kwargs
            captured["model"] = kwargs.get("model")
            captured["messages"] = kwargs.get("messages")

        asyncio.run(run())

    assert captured["model"] == "deepseek-reasoner"
    sys_content = captured["messages"][0]["content"]
    assert "[本对话人设 / 角色设定]" in sys_content
    assert "你是一个严谨的 Python 代码审查员" in sys_content


def test_model_default_when_none():
    """未指定 model 时回落到全局默认模型。"""
    captured = {}

    fake_resp = MagicMock()
    fake_resp.usage = None
    fake_choice = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = "ok"
    fake_msg.tool_calls = None
    fake_choice.message = fake_msg
    fake_resp.choices = [fake_choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch.object(llm, "AsyncOpenAI", return_value=fake_client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.config, "DEEPSEEK_MODEL", "deepseek-chat"), patch.object(
        llm.db, "get_all_memory", return_value=[]
    ):

        async def run():
            async for _ in llm.chat_stream(
                user_message="hi", conversation_id="c2", history=[]
            ):
                pass
            captured["model"] = fake_client.chat.completions.create.call_args.kwargs.get(
                "model"
            )

        asyncio.run(run())

    assert captured["model"] == "deepseek-chat"
