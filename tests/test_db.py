"""db.py 持久化层单元测试：会话/消息增删改查、work_dir、usage、搜索、长期记忆。

所有测试经由 temp_db fixture 使用临时库，不会污染真实 mini_wb.db。
"""
from app import db


def test_create_and_list(temp_db):
    temp_db.create_conversation("c1", "标题测试")
    rows = temp_db.list_conversations()
    assert "c1" in [r["id"] for r in rows]
    c = next(r for r in rows if r["id"] == "c1")
    assert c["title"] == "标题测试"
    assert c["work_dir"] == ""


def test_add_and_get_history(temp_db):
    temp_db.create_conversation("c2")
    temp_db.add_message("c2", "user", "你好")
    temp_db.add_message("c2", "assistant", "我是助手")
    hist = temp_db.get_history("c2")
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["content"] == "我是助手"


def test_get_history_with_tool_calls(temp_db):
    temp_db.create_conversation("c3")
    tcs = [{
        "id": "x1",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
        "result": "文件内容",
    }]
    temp_db.add_message("c3", "assistant", "读取中", tool_calls=tcs)
    hist = temp_db.get_history("c3")
    assert hist[0]["tool_calls"][0]["result"] == "文件内容"
    # tool_calls 必须被 JSON 反序列化回列表
    assert isinstance(hist[0]["tool_calls"], list)


def test_rename(temp_db):
    temp_db.create_conversation("c4")
    temp_db.rename_conversation("c4", "新名字")
    rows = temp_db.list_conversations()
    assert next(r["title"] for r in rows if r["id"] == "c4") == "新名字"


def test_delete(temp_db):
    temp_db.create_conversation("c5")
    temp_db.delete_conversation("c5")
    assert "c5" not in [r["id"] for r in temp_db.list_conversations()]


def test_work_dir_roundtrip(temp_db):
    temp_db.create_conversation("c6")
    assert temp_db.get_work_dir("c6") is None
    temp_db.set_conversation_work_dir("c6", "D:/work")
    assert temp_db.get_work_dir("c6") == "D:/work"
    temp_db.set_conversation_work_dir("c6", None)
    assert temp_db.get_work_dir("c6") is None


def test_work_dir_in_list(temp_db):
    temp_db.create_conversation("c7", work_dir="D:/x")
    rows = temp_db.list_conversations()
    assert next(r["work_dir"] for r in rows if r["id"] == "c7") == "D:/x"


def test_usage(temp_db):
    temp_db.create_conversation("c8")
    temp_db.add_usage("c8", 100)
    temp_db.add_usage("c8", 50)
    rows = temp_db.list_conversations()
    assert next(r["usage_total"] for r in rows if r["id"] == "c8") == 150


def test_search(temp_db):
    temp_db.create_conversation("c9")
    temp_db.add_message("c9", "user", "苹果香蕉橙子")
    res = temp_db.search_messages("香蕉")
    assert any(r["conversation_id"] == "c9" for r in res)
    assert temp_db.search_messages("不存在的关键词xyz") == []


def test_memory(temp_db):
    temp_db.upsert_memory("名字", "小明")
    mem = temp_db.get_all_memory()
    assert ("名字", "小明") in [(m["key"], m["value"]) for m in mem]
    # 同 key 覆盖
    temp_db.upsert_memory("名字", "小红")
    mem = temp_db.get_all_memory()
    vals = [m["value"] for m in mem if m["key"] == "名字"]
    assert vals == ["小红"]
    temp_db.delete_memory("名字")
    assert temp_db.get_all_memory() == []


def test_conversation_config_roundtrip(temp_db):
    temp_db.create_conversation(
        "c10", title="t", work_dir="D:/w", model="deepseek-reasoner", persona="你是审查员"
    )
    cfg = temp_db.get_conversation_config("c10")
    assert cfg["work_dir"] == "D:/w"
    assert cfg["model"] == "deepseek-reasoner"
    assert cfg["persona"] == "你是审查员"
    # 清空（空字符串视为清除，回退默认）
    temp_db.set_conversation_config("c10", work_dir="", model="deepseek-chat", persona="")
    cfg2 = temp_db.get_conversation_config("c10")
    assert cfg2["work_dir"] == ""
    assert cfg2["model"] == "deepseek-chat"
    assert cfg2["persona"] == ""
    # list_conversations 应回传 model / persona
    rows = temp_db.list_conversations()
    c = next(r for r in rows if r["id"] == "c10")
    assert c["model"] == "deepseek-chat"
    assert c["persona"] == ""


def test_get_config_missing(temp_db):
    assert temp_db.get_conversation_config("nope") == {
        "work_dir": "",
        "model": "",
        "persona": "",
        "disabled_tools": [],
    }

