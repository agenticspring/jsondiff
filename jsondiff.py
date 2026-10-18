#!/usr/bin/env python3
"""
jsondiff — semantic JSON diff CLI.

Compare two JSON files recursively and report differences with
JSON-pointer-style paths.

Usage:
    python3 -m jsondiff old.json new.json
    python3 -m jsondiff old.json new.json --json --array-key id
    python3 -m jsondiff old.json new.json --ignore 'meta.timestamp,debug'

Exit codes:
    0 — no differences
    1 — differences found
    2 — error (missing file, invalid JSON, bad arguments)

Standard library only (argparse, json, sys, re). Python 3.8+.
"""

import argparse
import json
import re
import sys

# ---------------------------------------------------------------------------
# Diff entry model
# ---------------------------------------------------------------------------
# An entry is a plain dict:
#   {"path": "a.b[0]", "op": "added|removed|changed|type_changed",
#    "old": <value or None>, "new": <value or None>}
# Entries are produced lazily by a generator so the text reporter can stream
# arbitrarily large diffs without building the whole list in memory.

OP_ADDED = "added"
OP_REMOVED = "removed"
OP_CHANGED = "changed"
OP_TYPE_CHANGED = "type_changed"

_MISSING = object()  # sentinel for "no value on this side"


# ---------------------------------------------------------------------------
# Type helpers (JSON semantics: bool is NOT a number)
# ---------------------------------------------------------------------------
def _json_type(value):
    """Return the JSON type name of a decoded value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _values_equal(old, new):
    """Semantic equality for JSON scalars.

    Numbers compare numerically (1 == 1.0) but bools never equal numbers,
    and strings only equal strings.
    """
    if _json_type(old) != _json_type(new):
        return False
    if isinstance(old, bool) or isinstance(old, (int, float)):
        return old == new
    return old == new


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------
def _seg_to_str(seg):
    """Canonical string for one path segment.

    Segments are: dict keys (str), list indices (int),
    or keyed-array labels as str already rendered like '[id=5]'.
    """
    if isinstance(seg, int) and not isinstance(seg, bool):
        return "[%d]" % seg
    return str(seg)


def _join_path(segments):
    """Render path segments in JSON-pointer-ish style: a.b[0].c"""
    out = []
    for seg in segments:
        s = _seg_to_str(seg)
        if s.startswith("["):
            out.append(s)
        elif out:
            out.append("." + s)
        else:
            out.append(s)
    return "".join(out)


_TOKEN_RE = re.compile(r"\[.*?\]|[^.\[\]]+")


def _parse_pattern(pattern):
    """Split an --ignore pattern like 'a.b[0],meta.timestamp' into segments."""
    return [tok for tok in _TOKEN_RE.findall(pattern.strip()) if tok]


def _pattern_matches(pattern_segs, path_segs):
    """True if pattern is a prefix of the path (segment-wise).

    A pattern segment of '*' matches any single path segment.
    Prefix matching means ignoring 'a.b' also ignores 'a.b.c'.
    """
    p_strs = [str(s) for s in pattern_segs]
    s_strs = [_seg_to_str(s) for s in path_segs]
    if len(p_strs) > len(s_strs):
        return False
    for p, s in zip(p_strs, s_strs):
        if p != "*" and p != s:
            return False
    return True


def _is_ignored(path_segs, ignore_patterns):
    return any(_pattern_matches(p, path_segs) for p in ignore_patterns)


# ---------------------------------------------------------------------------
# Core diff (generator — streams entries, O(depth) memory for text output)
# ---------------------------------------------------------------------------
def _entry(path_segs, op, old=_MISSING, new=_MISSING):
    return {
        "path": _join_path(path_segs),
        "op": op,
        "old": None if old is _MISSING else old,
        "new": None if new is _MISSING else new,
    }


def _is_json_number(value):
    """True for int/float — never bool (bool is its own JSON type)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _diff_values(old, new, path, array_key, ignore_patterns):
    """Yield diff entries comparing old and new at the given path."""
    if _is_ignored(path, ignore_patterns):
        return

    if isinstance(old, dict) and isinstance(new, dict):
        yield from _diff_objects(old, new, path, array_key, ignore_patterns)
        return
    if isinstance(old, list) and isinstance(new, list):
        yield from _diff_arrays(old, new, path, array_key, ignore_patterns)
        return
    if _is_json_number(old) and _is_json_number(new):
        # 1 vs 1.0 is not a difference; 1 vs 1.5 is a value change,
        # never a "type change".
        if old != new:
            yield _entry(path, OP_CHANGED, old, new)
        return

    old_t, new_t = _json_type(old), _json_type(new)
    if old_t != new_t:
        yield _entry(path, OP_TYPE_CHANGED, old, new)
        return
    if not _values_equal(old, new):
        yield _entry(path, OP_CHANGED, old, new)


def _diff_objects(old, new, path, array_key, ignore_patterns):
    for key in old:
        if key not in new:
            sub = path + [key]
            if not _is_ignored(sub, ignore_patterns):
                yield _entry(sub, OP_REMOVED, old=old[key])
        else:
            yield from _diff_values(old[key], new[key], path + [key],
                                    array_key, ignore_patterns)
    for key in new:
        if key not in old:
            sub = path + [key]
            if not _is_ignored(sub, ignore_patterns):
                yield _entry(sub, OP_ADDED, new=new[key])


def _diff_arrays(old, new, path, array_key, ignore_patterns):
    if array_key and _keyed_compare_possible(old, new, array_key):
        yield from _diff_arrays_keyed(old, new, path, array_key, ignore_patterns)
    else:
        for i, (o_item, n_item) in enumerate(zip(old, new)):
            yield from _diff_values(o_item, n_item, path + [i],
                                    array_key, ignore_patterns)
        for i in range(len(new), len(old)):
            sub = path + [i]
            if not _is_ignored(sub, ignore_patterns):
                yield _entry(sub, OP_REMOVED, old=old[i])
        for i in range(len(old), len(new)):
            sub = path + [i]
            if not _is_ignored(sub, ignore_patterns):
                yield _entry(sub, OP_ADDED, new=new[i])


def _keyed_compare_possible(old, new, key):
    """Keyed comparison needs every element on both sides to be an object
    carrying the key. Mixed arrays fall back to by-index comparison."""
    if not old and not new:
        return True
    if not old or not new:
        return False
    return all(isinstance(x, dict) and key in x for x in old) and \
        all(isinstance(x, dict) and key in x for x in new)


def _keyed_label(key, value, occurrence):
    """Label like '[id=5]' or '[id=5#2]' for duplicate keys."""
    try:
        label = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        label = str(value)
    if occurrence > 0:
        return "[%s=%s#%d]" % (key, label, occurrence + 1)
    return "[%s=%s]" % (key, label)


def _diff_arrays_keyed(old, new, path, key, ignore_patterns):
    def bucket(items):
        # (key_value_json, occurrence) -> (index, item), preserving order
        seen = {}
        out = {}
        for idx, item in enumerate(items):
            k = json.dumps(item[key], sort_keys=True, ensure_ascii=False)
            occ = seen.get(k, 0)
            seen[k] = occ + 1
            out[(k, occ)] = (idx, item)
        return out

    old_b, new_b = bucket(old), bucket(new)

    for k_occ in old_b:
        if k_occ not in new_b:
            idx, item = old_b[k_occ]
            k_json, occ = k_occ
            label = _keyed_label(key, json.loads(k_json), occ)
            sub = path + [label]
            if not _is_ignored(sub, ignore_patterns):
                yield _entry(sub, OP_REMOVED, old=item)
    for k_occ in new_b:
        if k_occ not in old_b:
            idx, item = new_b[k_occ]
            k_json, occ = k_occ
            label = _keyed_label(key, json.loads(k_json), occ)
            sub = path + [label]
            if not _is_ignored(sub, ignore_patterns):
                yield _entry(sub, OP_ADDED, new=item)
    for k_occ in old_b:
        if k_occ in new_b:
            _, o_item = old_b[k_occ]
            _, n_item = new_b[k_occ]
            k_json, occ = k_occ
            label = _keyed_label(key, json.loads(k_json), occ)
            yield from _diff_values(o_item, n_item, path + [label],
                                    key, ignore_patterns)


# ---------------------------------------------------------------------------
# Reporters
# ---------------------------------------------------------------------------
_OP_SYMBOL = {
    OP_ADDED: "+",
    OP_REMOVED: "-",
    OP_CHANGED: "~",
    OP_TYPE_CHANGED: "!",
}

_OP_COLOR = {
    OP_ADDED: "32",       # green
    OP_REMOVED: "31",     # red
    OP_CHANGED: "33",     # yellow
    OP_TYPE_CHANGED: "35",  # magenta
}


def _compact(value):
    """Single-line JSON rendering of a value for the text report."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          default=str)
    except (TypeError, ValueError):
        return str(value)


def report_text(entries, color):
    """Stream human-readable lines to stdout. Returns the entry count."""
    count = 0
    for e in entries:
        count += 1
        sym = _OP_SYMBOL[e["op"]]
        if e["op"] == OP_ADDED:
            line = "%s %s: %s" % (sym, e["path"], _compact(e["new"]))
        elif e["op"] == OP_REMOVED:
            line = "%s %s: %s" % (sym, e["path"], _compact(e["old"]))
        else:
            line = "%s %s: %s -> %s" % (sym, e["path"],
                                        _compact(e["old"]), _compact(e["new"]))
            if e["op"] == OP_TYPE_CHANGED:
                line += " (type: %s -> %s)" % (_json_type(e["old"]),
                                              _json_type(e["new"]))
        if color:
            line = "\033[%sm%s\033[0m" % (_OP_COLOR[e["op"]], line)
        sys.stdout.write(line + "\n")
    return count


def report_json(entries):
    """Collect entries and print one JSON array. Returns the entry count."""
    data = list(entries)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return len(data)


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------
def load_json(path):
    """Load a JSON file (or stdin when path == '-'). Raises on error."""
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
    except FileNotFoundError:
        raise ValueError("file not found: %s" % path)
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        label = "stdin" if path == "-" else path
        raise ValueError("invalid JSON in %s: %s" % (label, exc))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="jsondiff",
        description="Semantic diff of two JSON files with JSON-pointer-style "
                    "paths. Exit 0 = no diff, 1 = diff, 2 = error.",
    )
    p.add_argument("old", help="path to the old JSON file ('-' for stdin)")
    p.add_argument("new", help="path to the new JSON file ('-' for stdin)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable JSON output instead of text")
    p.add_argument("--ignore", default="",
                   help="comma-separated path patterns to skip, e.g. "
                        "'meta.timestamp,debug.*' (prefix match, * = any segment)")
    p.add_argument("--array-key", default=None,
                   help="compare arrays of objects by this key instead of index")
    p.add_argument("--color", choices=["auto", "always", "never"],
                   default="auto",
                   help="colorize text output (default: auto = when a TTY)")
    p.add_argument("--version", action="version", version="jsondiff 1.0.0")
    return p


def parse_ignore(value):
    if not value or not value.strip():
        return []
    return [_parse_pattern(part) for part in value.split(",") if part.strip()]


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    ignore_patterns = parse_ignore(args.ignore)

    if args.color == "always":
        use_color = True
    elif args.color == "never":
        use_color = False
    else:
        use_color = sys.stdout.isatty() and not args.as_json

    try:
        old = load_json(args.old)
        new = load_json(args.new)
    except ValueError as exc:
        sys.stderr.write("jsondiff: error: %s\n" % exc)
        return 2

    entries = _diff_values(old, new, [], args.array_key, ignore_patterns)

    if args.as_json:
        count = report_json(entries)
    else:
        count = report_text(entries, use_color)

    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main())
