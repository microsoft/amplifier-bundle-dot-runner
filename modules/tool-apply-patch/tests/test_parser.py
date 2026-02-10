"""Tests for v4a patch format parser."""

from amplifier_module_tool_apply_patch.parser import parse_v4a_patch


def test_add_file():
    patch = """*** Begin Patch
*** Add File: src/hello.py
+print("hello world")
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 1
    assert ops[0].operation == "add_file"
    assert ops[0].path == "src/hello.py"
    assert ops[0].content == 'print("hello world")\n'


def test_add_file_multiple_lines():
    patch = """*** Begin Patch
*** Add File: src/utils.py
+def greet(name):
+    return f"Hello, {name}!"
+
+def farewell(name):
+    return f"Goodbye, {name}!"
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 1
    assert ops[0].operation == "add_file"
    assert ops[0].path == "src/utils.py"
    expected = 'def greet(name):\n    return f"Hello, {name}!"\n\ndef farewell(name):\n    return f"Goodbye, {name}!"\n'
    assert ops[0].content == expected


def test_delete_file():
    patch = """*** Begin Patch
*** Delete File: old.py
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 1
    assert ops[0].operation == "delete_file"
    assert ops[0].path == "old.py"


def test_update_file_with_hunk():
    patch = """*** Begin Patch
*** Update File: src/main.py
@@ def main():
     print("old")
-    print("old")
+    print("new")
     return 0
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 1
    assert ops[0].operation == "update_file"
    assert ops[0].path == "src/main.py"
    assert len(ops[0].hunks) == 1
    hunk = ops[0].hunks[0]
    assert hunk.context_hint == "def main():"


def test_update_file_hunk_lines():
    """Verify hunk line types are correctly parsed."""
    patch = """*** Begin Patch
*** Update File: src/main.py
@@ def main():
     print("old")
-    print("old")
+    print("new")
     return 0
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    hunk = ops[0].hunks[0]
    # context, delete, add, context
    assert len(hunk.lines) == 4
    assert hunk.lines[0] == ("context", '    print("old")')
    assert hunk.lines[1] == ("delete", '    print("old")')
    assert hunk.lines[2] == ("add", '    print("new")')
    assert hunk.lines[3] == ("context", "    return 0")


def test_multiple_hunks_in_update():
    """PATCH-011: single Update File block can contain multiple @@ hunks."""
    patch = """*** Begin Patch
*** Update File: src/config.py
@@ DEFAULT_TIMEOUT = 30
-DEFAULT_TIMEOUT = 30
+DEFAULT_TIMEOUT = 60
@@ def load_config():
     config = {}
-    config["debug"] = False
+    config["debug"] = True
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 1
    assert ops[0].operation == "update_file"
    assert len(ops[0].hunks) == 2
    assert ops[0].hunks[0].context_hint == "DEFAULT_TIMEOUT = 30"
    assert ops[0].hunks[1].context_hint == "def load_config():"


def test_move_file():
    """PATCH-006: *** Move to: new_path."""
    patch = """*** Begin Patch
*** Update File: old_name.py
*** Move to: new_name.py
@@ import os
 import sys
-import old_dep
+import new_dep
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 1
    assert ops[0].operation == "update_file"
    assert ops[0].path == "old_name.py"
    assert ops[0].move_to == "new_name.py"


def test_multiple_operations():
    """Patch with multiple file operations."""
    patch = """*** Begin Patch
*** Add File: new.py
+print("new")
*** Delete File: old.py
*** Update File: main.py
@@ import os
-import old
+import new
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert len(ops) == 3
    assert ops[0].operation == "add_file"
    assert ops[1].operation == "delete_file"
    assert ops[2].operation == "update_file"


def test_empty_add_line():
    """Lines with just + and nothing else represent empty lines in added content."""
    patch = """*** Begin Patch
*** Add File: test.py
+line1
+
+line3
*** End Patch
"""
    ops = parse_v4a_patch(patch)
    assert ops[0].content == "line1\n\nline3\n"


def test_missing_begin_patch_raises():
    """Patch without *** Begin Patch header should raise."""
    patch = """*** Add File: test.py
+line1
*** End Patch
"""
    import pytest

    with pytest.raises(ValueError, match="Begin Patch"):
        parse_v4a_patch(patch)


def test_missing_end_patch_raises():
    """Patch without *** End Patch footer should raise."""
    patch = """*** Begin Patch
*** Add File: test.py
+line1
"""
    import pytest

    with pytest.raises(ValueError, match="End Patch"):
        parse_v4a_patch(patch)
