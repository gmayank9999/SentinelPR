from sentinel.retrieval.diff_parser import DiffContext, parse_unified_diff

BASE = '''import math


def area(r):
    return math.pi * r * r


class Shape:
    sides = 0

    def describe(self):
        return "shape"

    def old_name(self, x):
        total = 0
        for i in range(x):
            total += i
        return total
'''

HEAD = '''import math

LIMIT = 10


def area(r, precision=2):
    return round(math.pi * r * r, precision)


class Shape:
    sides = 0

    def describe(self):
        return "shape"

    def new_name(self, x):
        total = 0
        for i in range(x):
            total += i
        return total


def perimeter(r):
    return 2 * math.pi * r
'''


def test_parse_unified_diff_line_numbers():
    diff = """diff --git a/a.py b/a.py
index 1..2 100644
--- a/a.py
+++ b/a.py
@@ -3,0 +4,2 @@ x
+new line
+another
@@ -10 +12 @@ y
-old
+replacement
diff --git a/b.bin b/b.bin
Binary files a/b.bin and b/b.bin differ
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""
    changes = {c.path: c for c in parse_unified_diff(diff)}
    assert changes["a.py"].added_lines == [4, 5, 12]
    assert changes["a.py"].removed_lines == [10]
    assert changes["b.bin"].is_binary
    assert changes["new.py"].status == "renamed" and changes["new.py"].old_path == "old.py"
    assert changes["gone.py"].status == "removed" and changes["gone.py"].removed_lines == [1, 2]


def test_change_units_from_git(git_repo):
    base = git_repo.commit("base", {"pkg/geo.py": BASE, "other/x.py": "x = 1\n"})
    head = git_repo.commit("head", {"pkg/geo.py": HEAD, "other/x.py": "x = 2\n"})
    ctx = DiffContext(git_repo.path, base, head, prefix="pkg/")
    changes = ctx.file_changes()
    assert [c.path for c in changes] == ["geo.py"]  # scoped to the project and prefix stripped

    units = {u.qualname: u for u in ctx.change_units(changes)}
    assert set(units) == {"<module>", "area", "Shape.new_name", "perimeter"}
    assert units["area"].change_type == "modified"
    assert units["area"].signature_changed
    assert units["area"].old_signature == "def area(r)"
    assert units["Shape.new_name"].change_type == "renamed"
    assert units["perimeter"].change_type == "added"
    assert units["<module>"].changed_lines == [3]
    assert "Shape.describe" not in units


def test_working_tree_diff(git_repo):
    base = git_repo.commit("base", {"geo.py": BASE})
    git_repo.write("geo.py", BASE.replace('return "shape"', 'return "polygon"'))
    units = DiffContext(git_repo.path, base, None).change_units()
    assert [u.qualname for u in units] == ["Shape.describe"]
    assert units[0].changed_lines == [12]
