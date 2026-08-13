import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 项目根目录（app/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 文件操作沙箱根目录：所有读写都限制在此目录内
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", str(_PROJECT_ROOT))).resolve()

# 允许"目录操作"工具管理的具体目录（逗号分隔，默认指向工作区内的 target_dir）
_DEFAULT_TARGET = os.getenv("TARGET_DIR", str(WORKSPACE_ROOT / "target_dir"))
TARGET_DIR = Path(_DEFAULT_TARGET).resolve()

# RAG 检索的文档根目录（默认指向工作区内的 docs）
DOC_ROOT = Path(os.getenv("DOC_ROOT", str(WORKSPACE_ROOT / "docs"))).resolve()

# 操作系统识别：用于系统提示词与命令护栏
IS_WINDOWS = os.name == "nt" or os.getenv("OS", "").lower().startswith("windows")
OS_NAME = "Windows" if IS_WINDOWS else ("macOS" if os.uname().sysname == "Darwin" else "Linux")

# 文件沙箱开关：
#   - 网页版(True，默认)：文件操作限制在 WORKSPACE_ROOT 内，防越权
#   - 桌面版(False)：本地可信程序，允许用户选择真实本地文件并读写
MB_SANDBOX = os.getenv("MB_SANDBOX", "true" if not os.getenv("MB_DESKTOP") else "false").lower() != "false"


class Config:
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    DEEPSEEK_VISION_MODEL: str = os.getenv("DEEPSEEK_VISION_MODEL", "")
    MAX_AGENT_ITERATIONS: int = int(os.getenv("MAX_AGENT_ITERATIONS", "8"))
    DB_PATH: str = os.getenv("DB_PATH", str(_PROJECT_ROOT / "mini_wb.db"))
    WORKSPACE_ROOT: Path = WORKSPACE_ROOT
    TARGET_DIR: Path = TARGET_DIR
    DOC_ROOT: Path = DOC_ROOT
    MB_SANDBOX: bool = MB_SANDBOX
    IS_WINDOWS: bool = IS_WINDOWS
    OS_NAME: str = OS_NAME
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))


config = Config()
