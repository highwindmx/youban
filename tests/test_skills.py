"""skills.py 安全护栏单元测试：沙箱路径边界 + run_command 命令护栏 + git_op 白名单 + manage_dir 删除护栏。

命令的真实执行被 mock 掉（patch _run_subprocess），只验证护栏逻辑，
既跨平台稳定，又不真去执行任何 shell / git。
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from app.config import config as cfg
from app import skills


@pytest.fixture
def fs_root(tmp_path):
    """把 WORKSPACE_ROOT / TARGET_DIR 指向临时目录，并开启沙箱。"""
    root = tmp_path / "ws"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    with patch.object(cfg, "WORKSPACE_ROOT", root), \
         patch.object(cfg, "TARGET_DIR", target), \
         patch.object(cfg, "MB_SANDBOX", True):
        yield root, target


# ---------------- 沙箱路径边界 ----------------
def test_safe_path_inside(fs_root):
    root, _ = fs_root
    p = skills._safe_path("a.txt")
    assert p == (root / "a.txt").resolve()


def test_safe_path_relative_subdir(fs_root):
    root, _ = fs_root
    p = skills._safe_path("sub/dir/x.txt")
    assert root in p.parents or p == root


def test_safe_path_relative_escape_blocked(fs_root):
    root, _ = fs_root
    with pytest.raises(ValueError):
        skills._safe_path("../escape.txt")


def test_safe_path_absolute_outside_blocked(fs_root):
    root, _ = fs_root
    abs_out = root.parent / "forbidden_abs.txt"
    with pytest.raises(ValueError):
        skills._safe_path(str(abs_out))


def test_safe_target_path_inside(fs_root):
    root, target = fs_root
    p = skills._safe_target_path("ok.txt")
    assert p == (target / "ok.txt").resolve()


def test_safe_target_path_escape_blocked(fs_root):
    root, target = fs_root
    with pytest.raises(ValueError):
        skills._safe_target_path("../escape.txt")


# ---------------- run_command 命令护栏 ----------------
def test_run_command_dangerous_bin_blocked(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="X") as m:
        r = skills.run_command("powershell Get-Date")
    assert "[已拦截]" in r
    m.assert_not_called()


def test_run_command_chain_operator_blocked(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="X") as m:
        r = skills.run_command("echo a; echo b")
    assert "[已拦截]" in r
    m.assert_not_called()


def test_run_command_wildcard_delete_blocked(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="X") as m:
        r = skills.run_command("del *")
    assert "[已拦截]" in r
    m.assert_not_called()


def test_run_command_allowed_runs(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="OUTPUT") as m:
        r = skills.run_command("echo hello")
    assert r == "OUTPUT"
    m.assert_called_once()


# ---------------- git_op 白名单与破坏性标志拦截 ----------------
def test_git_op_disallowed_subcommand_blocked(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="X") as m:
        r = skills.git_op("reset", "--hard")
    assert "[已拦截]" in r
    m.assert_not_called()


def test_git_op_force_flag_blocked(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="X") as m:
        r = skills.git_op("push", "--force")
    assert "[已拦截]" in r
    m.assert_not_called()


def test_git_op_meta_char_blocked(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="X") as m:
        r = skills.git_op("status", "; rm -rf /")
    assert "[已拦截]" in r
    m.assert_not_called()


def test_git_op_allowed_runs(fs_root):
    with patch.object(skills, "_run_subprocess", return_value="GITOUT") as m:
        r = skills.git_op("status", "")
    assert r == "GITOUT"
    m.assert_called_once()


# ---------------- manage_dir 删除护栏 ----------------
def test_manage_dir_delete_root_blocked(fs_root):
    root, target = fs_root
    assert "[已拦截]" in skills.manage_dir("delete", ".")


def test_manage_dir_delete_dots_blocked(fs_root):
    root, target = fs_root
    assert "[已拦截]" in skills.manage_dir("delete", "..")


def test_manage_dir_delete_wildcard_blocked(fs_root):
    root, target = fs_root
    assert "[已拦截]" in skills.manage_dir("delete", "*.txt")


def test_manage_dir_write_delete_roundtrip(fs_root):
    root, target = fs_root
    r = skills.manage_dir("write", "f.txt", "hello")
    assert "已写入" in r
    assert (target / "f.txt").read_text(encoding="utf-8") == "hello"
    d = skills.manage_dir("delete", "f.txt")
    assert "已删除" in d
    assert not (target / "f.txt").exists()
