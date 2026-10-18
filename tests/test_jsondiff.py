#!/usr/bin/env python3
"""Unittest suite for jsondiff. Run:  python3 -m unittest discover -s tests -v"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jsondiff


def diff(old, new, array_key=None, ignore=""):
    pats = jsondiff.parse_ignore(ignore)
    return list(jsondiff._diff_values(old, new, [], array_key, pats))


class TestBasicDiff(unittest.TestCase):
    def test_no_diff(self):
        self.assertEqual(diff({"a": 1}, {"a": 1}), [])

    def test_added_removed_changed(self):
        entries = diff({"a": 1, "b": 2}, {"a": 1, "c": 3})
        ops = {(e["path"], e["op"]): (e["old"], e["new"]) for e in entries}
        self.assertEqual(ops[("b", "removed")], (2, None))
        self.assertEqual(ops[("c", "added")], (None, 3))

    def test_changed_value(self):
        entries = diff({"a": 1}, {"a": 2})
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual((e["path"], e["op"], e["old"], e["new"]),
                         ("a", "changed", 1, 2))

    def test_type_change_int_vs_str(self):
        entries = diff({"a": 1}, {"a": "1"})
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["op"], "type_changed")
        self.assertEqual((e["old"], e["new"]), (1, "1"))

    def test_bool_is_not_number(self):
        # True vs 1 must be a type change, not "equal"
        entries = diff({"a": True}, {"a": 1})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["op"], "type_changed")

    def test_int_float_equal(self):
        self.assertEqual(diff({"a": 1}, {"a": 1.0}), [])

    def test_int_float_unequal_is_changed_not_type(self):
        entries = diff({"a": 1}, {"a": 1.5})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["op"], "changed")

    def test_null_changes(self):
        entries = diff({"a": None}, {"a": 0})
        self.assertEqual(entries[0]["op"], "type_changed")


class TestNested(unittest.TestCase):
    def test_deep_path(self):
        entries = diff({"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 2}}})
        self.assertEqual(entries[0]["path"], "a.b.c")
        self.assertEqual(entries[0]["op"], "changed")

    def test_nested_array_index_path(self):
        entries = diff({"a": {"b": [10, 20]}}, {"a": {"b": [10, 99]}})
        self.assertEqual(entries[0]["path"], "a.b[1]")
        self.assertEqual(entries[0]["new"], 99)

    def test_deep_nesting_50_levels(self):
        def nest(depth, leaf):
            d = leaf
            for i in range(depth):
                d = {"k%d" % i: d}
            return d
        entries = diff(nest(50, 1), nest(50, 2))
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["path"].startswith("k49."))

    def test_object_replaced_by_scalar(self):
        entries = diff({"a": {"x": 1}}, {"a": 5})
        self.assertEqual(entries[0]["op"], "type_changed")


class TestArrays(unittest.TestCase):
    def test_by_index(self):
        entries = diff([1, 2, 3], [1, 9, 3])
        self.assertEqual([(e["path"], e["op"]) for e in entries],
                         [("[1]", "changed")])

    def test_append_and_truncate(self):
        added = diff([1], [1, 2])
        self.assertEqual([(e["path"], e["op"]) for e in added],
                         [("[1]", "added")])
        removed = diff([1, 2], [1])
        self.assertEqual([(e["path"], e["op"]) for e in removed],
                         [("[1]", "removed")])

    def test_array_key_matching(self):
        old = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
        new = [{"id": 2, "v": "b2"}, {"id": 3, "v": "c"}]
        entries = diff(old, new, array_key="id")
        by_path = {e["path"]: e for e in entries}
        self.assertEqual(by_path["[id=2].v"]["op"], "changed")
        self.assertEqual(by_path["[id=1]"]["op"], "removed")
        self.assertEqual(by_path["[id=3]"]["op"], "added")

    def test_array_key_reorder_is_not_diff(self):
        old = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
        new = [{"id": 2, "v": "b"}, {"id": 1, "v": "a"}]
        self.assertEqual(diff(old, new, array_key="id"), [])

    def test_array_key_fallback_without_key(self):
        # no --array-key given: plain by-index comparison
        old = [{"id": 1}, {"id": 2}]
        new = [{"id": 2}, {"id": 1}]
        entries = diff(old, new)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e["op"] == "changed" for e in entries))

    def test_array_key_missing_key_falls_back(self):
        old = [{"id": 1}, {"noid": True}]
        new = [{"id": 1}, {"noid": False}]
        entries = diff(old, new, array_key="id")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "[1].noid")

    def test_array_key_duplicate_keys(self):
        old = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}]
        new = [{"id": 1, "v": "a"}, {"id": 1, "v": "CHANGED"}]
        entries = diff(old, new, array_key="id")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "[id=1#2].v")

    def test_array_of_scalars_with_key_falls_back(self):
        entries = diff([1, 2], [1, 3], array_key="id")
        self.assertEqual([(e["path"], e["op"]) for e in entries],
                         [("[1]", "changed")])


class TestIgnore(unittest.TestCase):
    def test_ignore_single_path(self):
        entries = diff({"a": 1, "meta": {"ts": 1}}, {"a": 1, "meta": {"ts": 2}},
                       ignore="meta.ts")
        self.assertEqual(entries, [])

    def test_ignore_multiple_paths(self):
        entries = diff({"a": 1, "b": 2, "c": 3}, {"a": 9, "b": 9, "c": 3},
                       ignore="a,b")
        self.assertEqual(entries, [])

    def test_ignore_prefix_covers_children(self):
        entries = diff({"meta": {"ts": 1, "x": 1}}, {"meta": {"ts": 2, "x": 2}},
                       ignore="meta")
        self.assertEqual(entries, [])

    def test_ignore_wildcard(self):
        entries = diff({"debug": {"a": 1, "b": 2}}, {"debug": {"a": 9, "b": 9}},
                       ignore="debug.*")
        self.assertEqual(entries, [])

    def test_ignore_array_index(self):
        entries = diff({"items": [1, 2]}, {"items": [1, 9]},
                       ignore="items[1]")
        self.assertEqual(entries, [])

    def test_ignore_does_not_hide_siblings(self):
        entries = diff({"a": 1, "b": 2}, {"a": 9, "b": 9}, ignore="a")
        self.assertEqual([e["path"] for e in entries], ["b"])

    def test_ignore_empty_string(self):
        entries = diff({"a": 1}, {"a": 2}, ignore="")
        self.assertEqual(len(entries), 1)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _file(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = jsondiff.main(argv)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_no_diff(self):
        a = self._file("a.json", '{"x": 1}')
        b = self._file("b.json", '{"x": 1}')
        code, out, _ = self._run([a, b])
        self.assertEqual(code, 0)

    def test_exit_1_with_diff(self):
        a = self._file("a.json", '{"x": 1}')
        b = self._file("b.json", '{"x": 2}')
        code, out, _ = self._run([a, b, "--color", "never"])
        self.assertEqual(code, 1)
        self.assertIn("~ x: 1 -> 2", out)

    def test_exit_2_missing_file(self):
        a = self._file("a.json", '{"x": 1}')
        code, _, err = self._run([a, os.path.join(self.tmp.name, "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_exit_2_invalid_json(self):
        a = self._file("a.json", '{"x": 1}')
        bad = self._file("bad.json", '{"x": }')
        code, _, err = self._run([a, bad])
        self.assertEqual(code, 2)
        self.assertIn("invalid JSON", err)

    def test_exit_2_invalid_json_stdin_label(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("{oops")
        a = self._file("a.json", '{"x": 1}')
        try:
            code, _, err = self._run(["-", a])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 2)
        self.assertIn("stdin", err)

    def test_json_output_is_valid_json(self):
        a = self._file("a.json", '{"x": 1, "gone": true}')
        b = self._file("b.json", '{"x": 2, "fresh": [1]}')
        code, out, _ = self._run([a, b, "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(len(data), 3)
        self.assertTrue(all({"path", "op", "old", "new"} <= set(e) for e in data))

    def test_json_output_empty_array(self):
        a = self._file("a.json", '{}')
        code, out, _ = self._run([a, a, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_text_symbols(self):
        a = self._file("a.json", '{"gone": 1, "chg": "a"}')
        b = self._file("b.json", '{"chg": "b", "added": 2}')
        code, out, _ = self._run([a, b, "--color", "never"])
        self.assertEqual(code, 1)
        self.assertIn("- gone: 1", out)
        self.assertIn('+ added: 2', out)
        self.assertIn('~ chg: "a" -> "b"', out)

    def test_cli_ignore_and_array_key(self):
        a = self._file("a.json",
                       '{"users": [{"id": 1, "n": "a"}], "ts": 1}')
        b = self._file("b.json",
                       '{"users": [{"id": 1, "n": "b"}], "ts": 2}')
        code, out, _ = self._run([a, b, "--color", "never",
                                  "--array-key", "id", "--ignore", "ts"])
        self.assertEqual(code, 1)
        self.assertIn('[id=1].n: "a" -> "b"', out)
        self.assertNotIn("ts", out)

    def test_color_always_emits_ansi(self):
        a = self._file("a.json", '{"x": 1}')
        b = self._file("b.json", '{"x": 2}')
        _, out, _ = self._run([a, b, "--color", "always"])
        self.assertIn("\033[", out)


class TestEdgeCases(unittest.TestCase):
    def test_empty_objects(self):
        self.assertEqual(diff({}, {}), [])

    def test_root_arrays(self):
        entries = diff([1], [2])
        self.assertEqual(entries[0]["path"], "[0]")

    def test_root_scalars(self):
        self.assertEqual(diff(1, 1), [])
        entries = diff(1, 2)
        self.assertEqual((entries[0]["path"], entries[0]["op"]), ("", "changed"))

    def test_unicode_keys_and_values(self):
        entries = diff({"kunci": "lama"}, {"kunci": "baru"})
        self.assertEqual(entries[0]["path"], "kunci")
        self.assertEqual((entries[0]["old"], entries[0]["new"]), ("lama", "baru"))

    def test_large_array_streams(self):
        # 20k elements: generator must not blow up, diff is O(n)
        old = list(range(20000))
        new = list(range(20000))
        new[19999] = -1
        entries = diff(old, new)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "[19999]")

    def test_diff_is_lazy_generator(self):
        import types
        gen = jsondiff._diff_values({"a": 1}, {"a": 2}, [], None, [])
        self.assertIsInstance(gen, types.GeneratorType)


if __name__ == "__main__":
    unittest.main()
