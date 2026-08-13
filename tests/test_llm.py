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


# ---------------- 流式调用 mock 辅助 ----------------
def _delta(reasoning=None, content=None, tool_calls=None):
    d = MagicMock()
    d.reasoning_content = reasoning
    d.content = content
    d.tool_calls = tool_calls
    return d


def _tc_delta(idx, id=None, name=None, args=None):
    tc = MagicMock()
    tc.index = idx
    tc.id = id
    fn = MagicMock()
    fn.name = name
    fn.arguments = args
    tc.function = fn
    return tc


def _chunk(delta, usage=None, finish=None):
    ch = MagicMock()
    ch.usage = usage
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish
    ch.choices = [choice]
    return ch


async def _iter_chunks(chunks):
    for c in chunks:
        yield c


def _stream_client(chunks):
    """每次调用返回同一组 chunks 的【全新】异步迭代器（适合单阶段/循环场景）。"""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=lambda *a, **k: _iter_chunks(chunks)
    )
    return client


def _stream_client_phases(phases):
    """第 n 次调用返回 phases[n] 的 chunks；耗尽后返回空流。"""
    it = iter(phases)

    async def create(*a, **k):
        try:
            chunks = next(it)
        except StopIteration:
            return
        for c in chunks:
            yield c

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=create)
    return client


# ---------------- _stream_response 解析 ----------------
def test_stream_response_parses_reasoning_content_and_tool_calls():
    """_stream_response 应正确组装分段到达的 reasoning_content 与 tool_calls，并带 usage。"""
    chunks = [
        _chunk(_delta(reasoning="思考A")),
        _chunk(_delta(reasoning="思考B")),
        _chunk(_delta(content="答", tool_calls=[_tc_delta(0, id="t1", name="read_file", args='{"p":')])),
        _chunk(_delta(content="案", tool_calls=[_tc_delta(0, name=None, args='"a"}')])),
        _chunk(_delta(content=""), finish="tool_calls",
               usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
    ]
    client = _stream_client(chunks)

    async def run():
        out = {}
        events = []
        async for kind, text in llm._stream_response(
            client, out, model="m", messages=[], tools=[], tool_choice="auto", temperature=0
        ):
            events.append((kind, text))
        return out, events

    out, events = asyncio.run(run())
    assert out["reasoning_content"] == "思考A思考B"
    assert out["content"] == "答案"
    assert out["tool_calls"][0].id == "t1"
    assert out["tool_calls"][0].function.name == "read_file"
    assert out["tool_calls"][0].function.arguments == '{"p":"a"}'
    assert out["usage"].total_tokens == 15
    assert ("reasoning", "思考A") in events
    assert ("token", "答") in events


# ---------------- 会话级 persona / model 注入 ----------------
def test_persona_and_model_injected():
    """chat_stream 应把 persona 写入系统提示、把 model 覆盖全局默认。"""
    captured = {}
    client = _stream_client([_chunk(_delta(content="ok"), finish="stop")])

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
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
            kwargs = client.chat.completions.create.call_args.kwargs
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
    client = _stream_client([_chunk(_delta(content="ok"), finish="stop")])

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.config, "DEEPSEEK_MODEL", "deepseek-chat"), patch.object(
        llm.db, "get_all_memory", return_value=[]
    ):

        async def run():
            async for _ in llm.chat_stream(
                user_message="hi", conversation_id="c2", history=[]
            ):
                pass
            captured["model"] = client.chat.completions.create.call_args.kwargs.get(
                "model"
            )

        asyncio.run(run())

    assert captured["model"] == "deepseek-chat"


# ---------------- 工具权限强制闸 ----------------
def test_disabled_tool_refused_and_not_executed(tmp_path):
    """被禁用的工具应被服务端拒绝、绝不执行；其余工具不受影响。"""
    executed = []

    async def fake_exec(name, args):
        executed.append(name)
        return "executed:" + name

    chunks = [
        _chunk(_delta(tool_calls=[_tc_delta(0, id="t1", name="run_command", args='{"command":"ls"}')]),
               finish="tool_calls"),
    ]
    client = _stream_client(chunks)

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.db, "get_all_memory", return_value=[]), patch.object(
        llm.db, "add_message", new=MagicMock()
    ), patch.object(llm.skills, "execute_tool", new=fake_exec):

        async def run():
            events = []
            async for ev in llm.chat_stream(
                user_message="帮我跑个命令",
                conversation_id="c_gate",
                history=[],
                work_dir=str(tmp_path),
                disabled_tools=["run_command"],
            ):
                events.append(ev)
            return events

        events = asyncio.run(run())

    # 禁用工具未被实际执行
    assert "run_command" not in executed
    # 且产出了「已禁用」的拒绝结果
    joined = "\n".join(events)
    assert "已禁用" in joined
    assert "run_command" in joined


def test_enabled_tool_still_executed(tmp_path):
    """未禁用的工具应照常执行（首轮工具调用，次轮最终文本）。"""
    executed = []

    async def fake_exec(name, args):
        executed.append(name)
        return "ok"

    phases = [
        [_chunk(_delta(tool_calls=[_tc_delta(0, id="t1", name="run_command", args='{"command":"ls"}')]),
                finish="tool_calls")],
        [_chunk(_delta(content="完成"), finish="stop")],
    ]
    client = _stream_client_phases(phases)

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.db, "get_all_memory", return_value=[]), patch.object(
        llm.db, "add_message", new=MagicMock()
    ), patch.object(llm.skills, "execute_tool", new=fake_exec):

        async def run():
            async for _ in llm.chat_stream(
                user_message="帮我跑个命令",
                conversation_id="c_gate2",
                history=[],
                work_dir=str(tmp_path),
                disabled_tools=[],
            ):
                pass

        asyncio.run(run())

    assert "run_command" in executed


# ---------------- Tier1 本对话小结注入 ----------------
def test_tier1_conv_memory_injected(tmp_path):
    """设了工作目录时，本对话小结应注入系统提示（标签 + 内容）。"""
    captured = {}
    wd = tmp_path / "project"
    wd.mkdir()
    client = _stream_client([_chunk(_delta(content="ok"), finish="stop")])

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.config, "MB_SANDBOX", False), patch.object(
        llm.db, "get_all_memory", return_value=[]
    ), patch.object(
        llm.skills, "read_conv_memory", return_value="PROJECT_MEMORY_MARKER"
    ):

        async def run():
            async for _ in llm.chat_stream(
                user_message="hi",
                conversation_id="c_tier1",
                history=[],
                work_dir=str(wd),
            ):
                pass
            captured["messages"] = (
                client.chat.completions.create.call_args.kwargs.get("messages")
            )

        asyncio.run(run())

    sys_content = captured["messages"][0]["content"]
    assert "[本对话小结（项目记忆" in sys_content
    assert "PROJECT_MEMORY_MARKER" in sys_content


# ---------------- 工作目录须反映到环境上下文（WORKSPACE_ROOT/TARGET_DIR） ----------------
def test_workdir_reflected_in_env_context(tmp_path):
    """设了工作目录时，系统提示的 WORKSPACE_ROOT/TARGET_DIR 必须指向该目录，
    而非全局友伴根目录——否则模型会误以为当前目录仍是友伴根目录（历史 bug）。"""
    captured = {}
    wd = tmp_path / "my_project"
    wd.mkdir()
    global_root = str(llm.config.WORKSPACE_ROOT)
    client = _stream_client([_chunk(_delta(content="ok"), finish="stop")])

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.config, "MB_SANDBOX", False), patch.object(
        llm.db, "get_all_memory", return_value=[]
    ):
        async def run():
            async for _ in llm.chat_stream(
                user_message="hi",
                conversation_id="c_env",
                history=[],
                work_dir=str(wd),
            ):
                pass
            captured["messages"] = (
                client.chat.completions.create.call_args.kwargs.get("messages")
            )

        asyncio.run(run())

    sys_content = captured["messages"][0]["content"]
    # 工作目录应作为 WORKSPACE_ROOT 出现
    assert f"工作区根目录(WORKSPACE_ROOT)：{str(wd)}" in sys_content
    assert f"可操作目录(TARGET_DIR)：{str(wd)}" in sys_content
    # 全局友伴根目录不得作为 WORKSPACE_ROOT 行的值出现（仅可能出现在说明里，故精确匹配行）
    assert f"工作区根目录(WORKSPACE_ROOT)：{global_root}" not in sys_content


def test_no_workdir_uses_global_root():
    """未设工作目录时，系统提示的 WORKSPACE_ROOT 回退为全局根目录。"""
    captured = {}
    global_root = str(llm.config.WORKSPACE_ROOT)
    client = _stream_client([_chunk(_delta(content="ok"), finish="stop")])

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.db, "get_all_memory", return_value=[]):
        async def run():
            async for _ in llm.chat_stream(
                user_message="hi",
                conversation_id="c_env_none",
                history=[],
            ):
                pass
            captured["messages"] = (
                client.chat.completions.create.call_args.kwargs.get("messages")
            )

        asyncio.run(run())

    sys_content = captured["messages"][0]["content"]
    assert f"工作区根目录(WORKSPACE_ROOT)：{global_root}" in sys_content


# ---------------- 深度思考 reasoning_content 流式透传 ----------------
def test_reasoning_event_emitted_for_reasoner():
    """deepseek-reasoner 的 reasoning_content 应以流式 reasoning 事件逐段透传。"""
    chunks = [
        _chunk(_delta(reasoning="让我先拆解问题：1)… 2)…")),
        _chunk(_delta(content="最终答案"), finish="stop"),
    ]
    client = _stream_client(chunks)

    with patch.object(llm, "AsyncOpenAI", return_value=client), patch.object(
        llm.config, "DEEPSEEK_API_KEY", "test-key"
    ), patch.object(llm.db, "get_all_memory", return_value=[]):

        async def run():
            events = []
            async for ev in llm.chat_stream(
                user_message="hi",
                conversation_id="c_reason",
                history=[],
                model="deepseek-reasoner",
            ):
                events.append(ev)
            return events

        events = asyncio.run(run())

    assert any(e.startswith("event: reasoning") for e in events)
    assert "让我先拆解问题" in "\n".join(events)
    # 最终答案仍通过 token/done 正常返回
    assert "最终答案" in "\n".join(events)

