from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str
    images: list[str] = []
    work_dir: str | None = None


class RenameRequest(BaseModel):
    title: str


class WorkDirRequest(BaseModel):
    work_dir: str | None = None


class ConvConfigRequest(BaseModel):
    """对话级配置：工作目录 / 模型 / 人设（均可空，空字符串表示清除）。"""

    work_dir: str | None = None
    model: str | None = None
    persona: str | None = None
