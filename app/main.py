from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import config
from app.llm import chat_stream
from app.schemas import ChatRequest, RenameRequest, WorkDirRequest

# 每个进行中的对话流对应一个中止事件；前端点「停止」即 set 它
_STOP_EVENTS: dict[str, asyncio.Event] = {}


def _new_conv_id() -> str:
    return "c_" + uuid.uuid4().hex[:12]


_STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 启动时初始化数据库（替代已弃用的 @app.on_event("startup")）
    db.init_db()
    yield


app = FastAPI(title="友伴", lifespan=_lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


# 提供图标等静态资源（favicon、apple-touch-icon 引用 /static/*）
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/api/conversations")
async def conversations():
    return {"conversations": db.list_conversations()}


@app.post("/api/conversations")
async def create_conv(conversation_id: str | None = None):
    """新建会话：在服务端真正落库，返回新会话 ID 与标题，便于前端侧栏即时同步。"""
    conv_id = conversation_id or _new_conv_id()
    db.create_conversation(conv_id)
    return {"id": conv_id, "title": "新对话"}


@app.get("/api/history/{conversation_id}")
async def history(conversation_id: str):
    return {
        "work_dir": db.get_work_dir(conversation_id) or "",
        "messages": db.get_history(conversation_id),
    }


@app.post("/api/conversations/{conversation_id}/workdir")
async def set_workdir(conversation_id: str, req: WorkDirRequest):
    """持久化会话级工作目录范围（前端 pick 后调用，打开历史对话时回显）。"""
    db.set_conversation_work_dir(conversation_id, req.work_dir or None)
    return {"ok": True, "work_dir": req.work_dir or ""}


@app.get("/api/search")
async def search(q: str = ""):
    """跨会话全文搜索消息内容。"""
    if not q or not q.strip():
        return {"results": []}
    return {"results": db.search_messages(q.strip())}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conv(conversation_id: str):
    db.delete_conversation(conversation_id)
    # 即便会话已不存在也返回成功，保证前端可正常清理 UI
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/rename")
async def rename_conv(conversation_id: str, req: RenameRequest):
    db.rename_conversation(conversation_id, req.title)
    return {"ok": True}


@app.post("/api/chat/stop")
async def stop_chat(conversation_id: str):
    """前端点「停止」时调用：触发对应对话流的中止事件。"""
    ev = _STOP_EVENTS.get(conversation_id)
    if ev:
        ev.set()
    return {"ok": True}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """拖拽/选择文件上传到工作区，返回真实可达的绝对路径。

    浏览器安全模型下拖拽只能拿到文件名、拿不到绝对路径，
    因此统一在此把文件内容写入工作区，再返回可操作的真实路径。
    """
    dest = config.TARGET_DIR
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    for f in files:
        # 防目录穿越：只用纯文件名
        safe_name = os.path.basename(f.filename or "file")
        # 单文件大小上限，防止超大文件撑爆内存
        if f.size is not None and f.size > max_bytes:
            return {
                "paths": saved,
                "error": (
                    f"文件 {safe_name} 超过上传上限 {config.MAX_UPLOAD_MB}MB，"
                    "已跳过；其余文件已保存。"
                ),
            }
        # 重名则加时间戳避免覆盖
        target = dest / safe_name
        if target.exists():
            from datetime import datetime

            stamp = datetime.now().strftime("%H%M%S")
            target = dest / f"{target.stem}_{stamp}{target.suffix}"
        data = await f.read()
        target.write_bytes(data)
        saved.append(str(target.resolve()))
    return {"paths": saved}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    conv_id = req.conversation_id or str(uuid.uuid4())
    db.create_conversation(conv_id, work_dir=req.work_dir or None)
    # 仅在会话首条消息时设置标题（避免后续消息覆盖，原逻辑每次都覆盖）
    if not db.get_history(conv_id):
        db.rename_conversation(conv_id, req.message[:30])
    db.add_message(conv_id, "user", req.message)

    history = db.get_history(conv_id, limit=40)

    stop_event = asyncio.Event()
    _STOP_EVENTS[conv_id] = stop_event

    async def event_gen():
        final_text = ""
        got_done = False
        try:
            async for chunk in chat_stream(
                req.message, conv_id, history, stop_event,
                images=req.images or None, work_dir=req.work_dir or None
            ):
                yield chunk
                # 稳健解析：优先采用 done 事件带回的完整文本；未带则回退累加 token
                if chunk.startswith("event: done"):
                    try:
                        data = json.loads(chunk.split("data: ", 1)[1])
                        content = data.get("content")
                        if content:
                            final_text = content
                            got_done = True
                    except Exception:  # noqa: BLE001
                        pass
                elif chunk.startswith("event: token") and not got_done:
                    try:
                        data = json.loads(chunk.split("data: ", 1)[1])
                        final_text += data.get("text", "")
                    except Exception:  # noqa: BLE001
                        pass
                elif chunk.startswith("event: usage"):
                    try:
                        data = json.loads(chunk.split("data: ", 1)[1])
                        db.add_usage(conv_id, data.get("total_tokens", 0))
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            _STOP_EVENTS.pop(conv_id, None)
            # 落库守卫：仅当存在非空（去空白）文本时才写入，避免空消息
            if final_text and final_text.strip():
                db.add_message(conv_id, "assistant", final_text)
            yield ""

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=False)
