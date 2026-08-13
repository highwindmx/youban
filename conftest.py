"""pytest 全局配置：保证 tests/ 能直接 import app（项目根加入 sys.path）。

同时提供一个干净的临时 SQLite fixture，所有 db 测试都不会触碰真实 mini_wb.db。
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import patch


@pytest.fixture
def temp_db(tmp_path):
    """每个测试一个干净的临时 SQLite（绝不触碰真实 mini_wb.db）。"""
    from app.config import config as cfg
    from app import db

    db_path = tmp_path / "test_mini_wb.db"
    with patch.object(cfg, "DB_PATH", str(db_path)):
        db.init_db()
        yield db
