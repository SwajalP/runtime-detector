"""Bind static call names to indexed regions.

Attribute calls such as ``self.service.renew`` used to collapse to the first
method whose short name was ``renew``, which made ``RenewalController.renew``
a self-loop and left ``SubscriptionService.renew`` with no callers. Resolution
now uses the receiver:

* ``self.method`` / ``cls.method`` → that method on the enclosing class
* ``self.attr.method`` → the class assigned to ``self.attr`` in ``__init__``
* a bare name imported in this file → that import's target
* a bare name defined in this file → that function or class
* ``Obj().method()`` → ``method`` on a class this function also constructs
* a name that matches exactly one function, or exactly one method

Builtins and methods of literals (``dict.get``, ``list.append``) stay unbound.
Ambiguous names stay unbound instead of linking to an arbitrary region.
"""

from __future__ import annotations

import ast
import sqlite3
import textwrap

_BUILTINS = {
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable",
    "chr", "classmethod", "compile", "complex", "delattr", "dict", "dir", "divmod",
    "enumerate", "eval", "exec", "filter", "float", "format", "frozenset", "getattr",
    "globals", "hasattr", "hash", "help", "hex", "id", "input", "int", "isinstance",
    "issubclass", "iter", "len", "list", "locals", "map", "max", "memoryview", "min",
    "next", "object", "oct", "open", "ord", "pow", "print", "property", "range",
    "repr", "reversed", "round", "set", "setattr", "slice", "sorted", "staticmethod",
    "str", "sum", "super", "tuple", "type", "vars", "zip", "None", "True", "False",
    "Exception", "ValueError", "TypeError", "KeyError", "AttributeError", "RuntimeError",
    "NotImplementedError", "StopIteration", "AssertionError",
}

# Methods of builtins and stdlib objects. Resolved only when the receiver is a
# project class that actually defines that method (``InvoiceRepository.get``).
_BUILTIN_METHODS = {
    "append", "extend", "pop", "insert", "remove", "clear", "copy", "get",
    "keys", "values", "items", "update", "setdefault", "add", "discard",
    "join", "split", "strip", "lstrip", "rstrip", "format", "lower", "upper",
    "startswith", "endswith", "replace", "write", "writelines", "read",
    "writerow", "writerows", "writeheader", "getvalue", "dumps", "loads",
    "today", "now", "isoformat", "fromisoformat", "strftime", "strptime",
    "encode", "decode", "hexdigest",
}


def resolve_callees(conn: sqlite3.Connection) -> int:
    """Recompute ``calls.callee_id`` for every row. Returns how many bound."""
    conn.execute("UPDATE calls SET callee_id = NULL")
    index = _Index.load(conn)
    bound = 0
    rows = conn.execute("SELECT caller_id, callee_name FROM calls").fetchall()
    for caller_id, callee_name in rows:
        target = index.resolve(caller_id, callee_name)
        if not target:
            continue
        conn.execute(
            "UPDATE calls SET callee_id = ? WHERE caller_id = ? AND callee_name = ?",
            (target, caller_id, callee_name),
        )
        bound += 1
    return bound


def _init_attr_types(body: str) -> dict[str, str]:
    """Map ``self.attr`` to a class name assigned in an ``__init__`` body."""
    try:
        tree = ast.parse(textwrap.dedent(body or ""))
    except SyntaxError:
        return {}
    fn = next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if fn is None:
        return {}
    params: dict[str, str] = {}
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs):
        name = _ann_name(arg.annotation)
        if name:
            params[arg.arg] = name
    found: dict[str, str] = {}
    for node in ast.walk(fn):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                type_name = _expr_type(value, params) if value is not None else None
                if type_name is None and isinstance(node, ast.AnnAssign):
                    type_name = _ann_name(node.annotation)
                if type_name:
                    found[target.attr] = type_name
    return found


def _ann_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id[:1].isupper():
        return node.id
    if isinstance(node, ast.Attribute) and node.attr[:1].isupper():
        return node.attr
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _ann_name(node.left) or _ann_name(node.right)
    if isinstance(node, ast.Subscript):
        return _ann_name(node.slice)
    return None


def _expr_type(node: ast.AST, params: dict[str, str]) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id[:1].isupper():
            return func.id
        if isinstance(func, ast.Attribute) and func.attr[:1].isupper():
            return func.attr
        return None
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            found = _expr_type(value, params)
            if found:
                return found
        return None
    if isinstance(node, ast.Name):
        if node.id[:1].isupper():
            return node.id
        return params.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr[:1].isupper():
        return node.attr
    return None


class _Index:
    def __init__(self) -> None:
        self.callers: dict[str, dict] = {}
        self.functions: dict[str, list[str]] = {}
        self.methods: dict[str, list[str]] = {}
        self.same_file: dict[tuple[str, str], str] = {}
        self.imports: dict[str, list[tuple[str, str | None]]] = {}
        self.attr_types: dict[tuple[str, str, str], str] = {}
        self.calls_by_caller: dict[str, list[str]] = {}
        self.path_of: dict[str, str] = {}

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "_Index":
        idx = cls()
        for row in conn.execute(
            "SELECT region_id, path, symbol, kind, body FROM source_regions WHERE kind != 'module'"
        ):
            symbol = row["symbol"] or ""
            idx.path_of[row["region_id"]] = row["path"]
            if row["kind"] == "function":
                idx.functions.setdefault(symbol, []).append(row["region_id"])
                idx.same_file[(row["path"], symbol)] = row["region_id"]
            elif row["kind"] == "class":
                idx.same_file[(row["path"], symbol)] = row["region_id"]
            elif row["kind"] == "method":
                idx.methods.setdefault(symbol, []).append(row["region_id"])
                idx.callers[row["region_id"]] = {
                    "path": row["path"],
                    "symbol": symbol,
                    "kind": row["kind"],
                    "class_name": symbol.rsplit(".", 1)[0] if "." in symbol else None,
                }
                if symbol.endswith(".__init__"):
                    class_name = symbol.rsplit(".", 1)[0]
                    for attr, type_name in _init_attr_types(row["body"] or "").items():
                        idx.attr_types[(row["path"], class_name, attr)] = type_name
            if row["kind"] in {"function", "method"} and row["region_id"] not in idx.callers:
                idx.callers[row["region_id"]] = {
                    "path": row["path"],
                    "symbol": symbol,
                    "kind": row["kind"],
                    "class_name": None,
                }
        for row in conn.execute(
            """
            SELECT m.path AS path, i.module AS module, i.name AS name
            FROM imports i
            JOIN source_regions m ON m.region_id = i.region_id
            """
        ):
            idx.imports.setdefault(row["path"], []).append((row["module"] or "", row["name"]))
        for row in conn.execute("SELECT caller_id, callee_name FROM calls"):
            idx.calls_by_caller.setdefault(row["caller_id"], []).append(row["callee_name"])
        return idx

    def resolve(self, caller_id: str, callee_name: str) -> str | None:
        caller = self.callers.get(caller_id)
        if caller is None or not callee_name:
            return None
        parts = callee_name.split(".")
        short = parts[-1]
        if short in _BUILTINS or callee_name in _BUILTINS or parts[0] in {"cls"} and len(parts) == 1:
            return None
        if parts[0] in {"self", "cls"} and len(parts) == 2:
            return self._self_method(caller, short)
        if parts[0] in {"self", "cls"} and len(parts) >= 3:
            return self._attr_method(caller, parts[1], short)
        if len(parts) >= 2:
            if short in _BUILTIN_METHODS:
                return None
            if parts[0][:1].isupper():
                return self._one(self.methods.get(f"{parts[0]}.{short}", []))
            return None
        if short in _BUILTIN_METHODS:
            return None
        imported = self._imported(caller["path"], short)
        if imported:
            return imported
        local = self.same_file.get((caller["path"], short))
        if local:
            return local
        constructed = self._constructed(caller_id, short)
        if constructed:
            return constructed
        unique_fn = self._one(self.functions.get(short, []))
        if unique_fn:
            return unique_fn
        return self._unique_method(short)

    def _self_method(self, caller: dict, short: str) -> str | None:
        class_name = caller.get("class_name")
        if not class_name:
            return None
        hits = self.methods.get(f"{class_name}.{short}", [])
        if short in _BUILTIN_METHODS and not hits:
            return None
        return self._one(hits)

    def _attr_method(self, caller: dict, attr: str, short: str) -> str | None:
        class_name = caller.get("class_name")
        type_name = None
        if class_name:
            type_name = self.attr_types.get((caller["path"], class_name, attr))
        if not type_name:
            return None
        return self._one(self.methods.get(f"{type_name}.{short}", []))

    def _imported(self, path: str, name: str) -> str | None:
        for module, imported in self.imports.get(path, []):
            if imported != name:
                continue
            target = module.replace(".", "/") + ".py"
            local = self.same_file.get((target, name))
            if local:
                return local
            method = self._one(self.methods.get(name, []))
            if method and self.path_of.get(method) == target:
                return method
            for symbol, ids in self.methods.items():
                if symbol.endswith(f".{name}"):
                    matched = [rid for rid in ids if self.path_of.get(rid) == target]
                    one = self._one(matched)
                    if one:
                        return one
        return None

    def _constructed(self, caller_id: str, method: str) -> str | None:
        found: list[str] = []
        for name in self.calls_by_caller.get(caller_id, []):
            if "." in name or not name[:1].isupper():
                continue
            hit = self._one(self.methods.get(f"{name}.{method}", []))
            if hit:
                found.append(hit)
        return self._one(list(dict.fromkeys(found)))

    def _unique_method(self, name: str) -> str | None:
        found: list[str] = []
        for symbol, ids in self.methods.items():
            if symbol == name or symbol.endswith(f".{name}"):
                found.extend(ids)
        return self._one(found)

    @staticmethod
    def _one(ids: list[str]) -> str | None:
        if len(ids) == 1:
            return ids[0]
        return None
