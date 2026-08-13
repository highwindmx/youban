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


# ---------------- 单对话小结（Tier1）文件读写 ----------------
def test_conv_summary_write_and_read(tmp_path):
    wd = tmp_path / "project"
    skills.write_conv_summary("c1", str(wd), "## 小结\n内容")
    assert skills.read_conv_memory("c1", str(wd)) == "## 小结\n内容"


def test_conv_memory_empty_without_workdir():
    assert skills.read_conv_memory("c1", "") == ""


def test_conv_note_upsert_and_replace(tmp_path):
    wd = tmp_path / "project"
    skills.upsert_conv_note("c1", str(wd), "约定", "用4空格")
    skills.upsert_conv_note("c1", str(wd), "约定", "用2空格")  # 同名覆盖
    text = (wd / ".youban/memories/c1.notes.md").read_text(encoding="utf-8")
    assert "用2空格" in text
    assert "用4空格" not in text
    # read_conv_memory 应合并小结(.md) + 要点(.notes.md)
    assert "用2空格" in skills.read_conv_memory("c1", str(wd))


def test_conv_note_delete(tmp_path):
    wd = tmp_path / "project"
    skills.upsert_conv_note("c1", str(wd), "约定", "用4空格")
    skills.delete_conv_note("c1", str(wd), "约定")
    assert "约定" not in skills.read_conv_memory("c1", str(wd))


# ---------------- remember scope 路由 ----------------
def test_remember_global_writes_db(temp_db):
    skills.set_work_dir(None)
    skills.set_conv_id(None)
    skills.remember("姓名", "张三", scope="global")
    mem = temp_db.get_all_memory()
    assert any(m["key"] == "姓名" and m["value"] == "张三" for m in mem)


def test_remember_project_writes_file(tmp_path):
    wd = tmp_path / "project"
    skills.set_work_dir(str(wd))
    skills.set_conv_id("c9")
    msg = skills.remember("约定", "用4空格", scope="project")
    assert "已记录项目记忆" in msg
    text = (wd / ".youban/memories/c9.notes.md").read_text(encoding="utf-8")
    assert "用4空格" in text
    skills.set_work_dir(None)
    skills.set_conv_id(None)


def test_forget_project_deletes_note(tmp_path):
    wd = tmp_path / "project"
    skills.set_work_dir(str(wd))
    skills.set_conv_id("c9")
    skills.remember("约定", "用4空格", scope="project")
    skills.forget("约定", scope="project")
    assert "约定" not in skills.read_conv_memory("c9", str(wd))
    skills.set_work_dir(None)
    skills.set_conv_id(None)

