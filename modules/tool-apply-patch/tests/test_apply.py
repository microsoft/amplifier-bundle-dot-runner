"""Tests for apply_patch tool execution."""

import pytest

from amplifier_module_tool_apply_patch import ApplyPatchTool


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_add_file(tmp_path):
    """Add File creates a new file with the given content."""
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = "*** Begin Patch\n*** Add File: hello.py\n+print('hi')\n*** End Patch\n"
    result = await tool.execute({"patch": patch})
    assert result.success
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
    assert "hello.py" in result.output["files_modified"]


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_add_file_nested_dirs(tmp_path):
    """Add File creates parent directories as needed."""
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = "*** Begin Patch\n*** Add File: src/utils/helpers.py\n+def greet():\n+    pass\n*** End Patch\n"
    result = await tool.execute({"patch": patch})
    assert result.success
    assert (tmp_path / "src" / "utils" / "helpers.py").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_delete_file(tmp_path):
    """Delete File removes an existing file."""
    (tmp_path / "old.py").write_text("old content")
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = "*** Begin Patch\n*** Delete File: old.py\n*** End Patch\n"
    result = await tool.execute({"patch": patch})
    assert result.success
    assert not (tmp_path / "old.py").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_update_file(tmp_path):
    """Update File applies hunk changes to an existing file."""
    (tmp_path / "main.py").write_text(
        "def main():\n    print('old')\n    print('old')\n    return 0\n"
    )
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = """*** Begin Patch
*** Update File: main.py
@@ def main():
     print('old')
-    print('old')
+    print('new')
     return 0
*** End Patch
"""
    result = await tool.execute({"patch": patch})
    assert result.success
    content = (tmp_path / "main.py").read_text()
    assert "print('new')" in content
    # The first print('old') is a context line (kept), the second was replaced
    assert content.count("print('old')") == 1
    assert content.count("print('new')") == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_update_multiple_hunks(tmp_path):
    """Update File with multiple hunks applies all changes."""
    (tmp_path / "config.py").write_text(
        'DEFAULT_TIMEOUT = 30\n\ndef load_config():\n    config = {}\n    config["debug"] = False\n    return config\n'
    )
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = """*** Begin Patch
*** Update File: config.py
@@ DEFAULT_TIMEOUT = 30
-DEFAULT_TIMEOUT = 30
+DEFAULT_TIMEOUT = 60
@@ def load_config():
     config = {}
-    config["debug"] = False
+    config["debug"] = True
*** End Patch
"""
    result = await tool.execute({"patch": patch})
    assert result.success
    content = (tmp_path / "config.py").read_text()
    assert "DEFAULT_TIMEOUT = 60" in content
    assert 'config["debug"] = True' in content


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_move_file(tmp_path):
    """Update+Move renames the file and applies changes."""
    (tmp_path / "old_name.py").write_text("import os\nimport sys\nimport old_dep\n")
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = """*** Begin Patch
*** Update File: old_name.py
*** Move to: new_name.py
@@ import os
 import sys
-import old_dep
+import new_dep
*** End Patch
"""
    result = await tool.execute({"patch": patch})
    assert result.success
    assert not (tmp_path / "old_name.py").exists()
    assert (tmp_path / "new_name.py").exists()
    content = (tmp_path / "new_name.py").read_text()
    assert "import new_dep" in content
    assert "import old_dep" not in content


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_update_nonexistent_file_fails(tmp_path):
    """Update File on a missing file returns error."""
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = """*** Begin Patch
*** Update File: missing.py
@@ def main():
-    old
+    new
*** End Patch
"""
    result = await tool.execute({"patch": patch})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_invalid_patch_fails(tmp_path):
    """Malformed patch content returns error."""
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    result = await tool.execute({"patch": "this is not a valid patch"})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_patch_param(tmp_path):
    """Missing patch parameter returns error."""
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    result = await tool.execute({})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_tool_name_and_description():
    """Tool has correct name and description."""
    tool = ApplyPatchTool(config={})
    assert tool.name == "apply_patch"
    assert "patch" in tool.description.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_tool_input_schema():
    """Tool exposes correct input schema."""
    tool = ApplyPatchTool(config={})
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "patch" in schema["properties"]
    assert "patch" in schema["required"]


@pytest.mark.asyncio(loop_scope="session")
async def test_summary_in_output(tmp_path):
    """Successful result includes a summary string."""
    tool = ApplyPatchTool(config={"working_dir": str(tmp_path)})
    patch = "*** Begin Patch\n*** Add File: test.py\n+pass\n*** End Patch\n"
    result = await tool.execute({"patch": patch})
    assert result.success
    assert "summary" in result.output
    assert isinstance(result.output["summary"], str)
