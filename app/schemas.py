from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str
    images: list[str] = []
    work_dir: str | None = None


class RenameRequest(BaseModel):
    title: str
