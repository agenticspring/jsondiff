# jsondiff

Semantic JSON diff CLI. Compare two JSON files recursively and report
differences with JSON-pointer-style paths — added (`+`), removed (`-`),
changed (`~`), and type changes (`!`).

Zero dependencies, standard library only (`argparse`, `json`, `sys`, `re`).
Python 3.8+.

## Installation

No installation needed. Clone and run:

```sh
git clone https://github.com/agenticspring/jsondiff.git
cd jsondiff
python3 -m jsondiff old.json new.json
```

Or run the single file directly:

```sh
./jsondiff.py old.json new.json
```

## Usage

```sh
python3 -m jsondiff old.json new.json
python3 -m jsondiff old.json new.json --json
python3 -m jsondiff old.json new.json --ignore 'meta.timestamp,debug.*'
python3 -m jsondiff old.json new.json --array-key id
echo '{"a":1}' | python3 -m jsondiff - new.json
```

### Example

`old.json`:

```json
{
  "name": "widget",
  "price": 10,
  "tags": ["red", "large"],
  "meta": {"rev": 3, "ts": 1700000000},
  "stock": 5
}
```

`new.json`:

```json
{
  "name": "widget",
  "price": "10",
  "tags": ["red", "small"],
  "meta": {"rev": 4, "ts": 1700000999},
  "sku": "W-1"
}
```

```sh
$ python3 -m jsondiff old.json new.json --ignore 'meta.ts' --color never
! price: 10 -> "10" (type: int -> str)
~ tags[1]: "large" -> "small"
~ meta.rev: 3 -> 4
- stock: 5
+ sku: "W-1"
```

### Array of objects by key

Without `--array-key`, arrays compare by index. With `--array-key id`,
objects are matched by their `id` — reordering is not a diff, and
additions/removals are reported per key:

```sh
$ python3 -m jsondiff users_old.json users_new.json --array-key id --color never
~ [id=2].name: "budi" -> "budi santoso"
- [id=3]
+ [id=7]: {"id": 7, "name": "sari"}
```

### Machine-readable output

```sh
$ python3 -m jsondiff old.json new.json --json
[
  {
    "path": "tags[1]",
    "op": "changed",
    "old": "large",
    "new": "small"
  }
]
```

Each entry has `path`, `op` (`added` | `removed` | `changed` |
`type_changed`), `old`, and `new` (`null` when absent on that side).

### Options

| Flag | Description |
|---|---|
| `--json` | Emit a JSON array of diff entries instead of text |
| `--ignore PATTERNS` | Comma-separated paths to skip, e.g. `'meta.timestamp,debug.*'`. A pattern also covers everything beneath it; `*` matches any single segment |
| `--array-key KEY` | Compare arrays of objects by `KEY` instead of index (falls back to by-index for non-object or key-less items) |
| `--color auto\|always\|never` | Colorize text output (default `auto`: only when stdout is a TTY) |
| `--version` | Print version and exit |

Use `-` in place of a file path to read that side from stdin.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | No differences |
| `1` | Differences found |
| `2` | Error (missing file, invalid JSON, bad arguments) |

This makes `jsondiff` usable directly in shell conditionals and CI:

```sh
if python3 -m jsondiff expected.json actual.json --color never; then
  echo "configs match"
fi
```

## Semantic rules

- Objects compare key-by-key, order-insensitive.
- Numbers compare numerically: `1` vs `1.0` is **not** a diff; `1` vs `1.5` is
  a value change (never a type change).
- `true` vs `1` **is** a type change — JSON booleans are not numbers.
- `1` vs `"1"` is a type change, reported as `!` with the old and new types.
- Arrays compare by index unless `--array-key` is given and every element on
  both sides is an object carrying that key. Duplicate keys are matched by
  occurrence (`[id=5]`, `[id=5#2]`).
- `--ignore` uses prefix matching: ignoring `meta` skips `meta.ts` too.
- Diff entries are generated lazily, so text output streams in O(depth)
  memory; each input file is parsed exactly once.

## Limitations

- JSON duplicate object keys: like Python's `json` module, the last value
  wins silently.
- Object keys containing dots or brackets (e.g. `"a.b"`) are ambiguous in
  path notation — they render identically to nested paths.
- `--json` output buffers all entries before printing (a single JSON array
  cannot stream); for very large diffs prefer the text reporter.
- `--array-key` only applies when *both* arrays consist entirely of objects
  containing the key; mixed arrays fall back to by-index comparison.
- Deeply recursive input is bounded by Python's recursion limit (default
  ~1000 levels).
- Floating-point comparisons use exact equality (`0.1 + 0.2 != 0.3` style
  artifacts apply).

## Testing

```sh
python3 -m unittest discover -s tests -v
```

The suite covers nested diffs, by-index vs by-key arrays, type changes,
ignore patterns (prefix + wildcard), exit codes 0/1/2, invalid JSON, and
stdin handling.

## License

MIT — see [LICENSE](LICENSE).
