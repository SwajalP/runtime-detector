"""Source-region extraction.

Primary backend: Tree-sitter (incremental, multilingual). Fallback: Python ``ast``.
Both produce the same ``ParsedFile`` so the rest of the system is backend-agnostic.
Tree-sitter supplies *syntax*; dynamic dispatch and cross-module resolution are
left to the runtime tracer and the heuristic callee resolver.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from ledger.config import LedgerConfig
from ledger.events.schema import SourceRegion, content_hash, estimate_tokens, region_id_for
from ledger.gitutil import git_commit, git_repo_id

try:  # optional dependency; wheels exist for CPython 3.9-3.14
    import tree_sitter_python as _tsp
    from tree_sitter import Language, Parser

    _TS_LANGUAGE = Language(_tsp.language())
    _TS_PARSER = Parser(_TS_LANGUAGE)
    TREE_SITTER_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when the wheel is missing
    _TS_PARSER = None
    TREE_SITTER_AVAILABLE = False


@dataclass
class ParsedFile:
    path: str
    regions: list[SourceRegion]
    calls: list[tuple[str, str]]
    imports: list[tuple[str, str, str | None]]
    backend: str = "ast"


def _qualified(stack: list[str], name: str) -> str:
    return ".".join([*stack, name]) if stack else name


class _RegionBuilder:
    """Shared region/ID construction for both parser backends."""

    def __init__(self, cfg: LedgerConfig, path: Path, source: str) -> None:
        self.repo_id = git_repo_id(cfg.repo_root)
        self.commit_sha = git_commit(cfg.repo_root)
        self.rel = str(path.relative_to(cfg.repo_root))
        self.lines = source.splitlines()
        self.source = source
        self.regions: list[SourceRegion] = []
        self.calls: list[tuple[str, str]] = []
        self.imports: list[tuple[str, str, str | None]] = []
        self.module_id = self._add_module()

    def slice_lines(self, start: int, end: int) -> str:
        return "\n".join(self.lines[start - 1 : end])

    def _add_module(self) -> str:
        module_hash = content_hash(self.source)
        end = max(1, len(self.lines))
        rid = region_id_for(self.repo_id, self.commit_sha, self.rel, 1, end, module_hash)
        self.regions.append(
            SourceRegion(
                region_id=rid,
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                path=self.rel,
                symbol=self.rel,
                kind="module",
                start_line=1,
                end_line=end,
                content_hash=module_hash,
                token_count=estimate_tokens(self.source),
                signature=self.rel,
                body=self.source[:4000],
            )
        )
        return rid

    def add_region(self, start: int, end: int, name: str, kind: str) -> str:
        end = max(start, end)
        body = self.slice_lines(start, end)
        ch = content_hash(body)
        rid = region_id_for(self.repo_id, self.commit_sha, self.rel, start, end, ch)
        sig = body.splitlines()[0].strip() if body else name
        self.regions.append(
            SourceRegion(
                region_id=rid,
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                path=self.rel,
                symbol=name,
                kind=kind,
                start_line=start,
                end_line=end,
                content_hash=ch,
                token_count=estimate_tokens(body),
                signature=sig[:240],
                body=body,
            )
        )
        return rid

    def finish(self, backend: str) -> ParsedFile:
        return ParsedFile(
            path=self.rel, regions=self.regions, calls=self.calls, imports=self.imports, backend=backend
        )


# --------------------------------------------------------------------------- #
# Tree-sitter backend
# --------------------------------------------------------------------------- #


def _ts_text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ts_call_name(node, src: bytes) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return _ts_text(fn, src)
    if fn.type == "attribute":
        parts: list[str] = []
        cur = fn
        while cur is not None and cur.type == "attribute":
            attr = cur.child_by_field_name("attribute")
            if attr is not None:
                parts.append(_ts_text(attr, src))
            cur = cur.child_by_field_name("object")
        if cur is not None and cur.type == "identifier":
            parts.append(_ts_text(cur, src))
        parts.reverse()
        return ".".join(parts) if parts else None
    return None


def _parse_with_treesitter(path: Path, source: str, cfg: LedgerConfig) -> ParsedFile:
    src = source.encode("utf-8")
    tree = _TS_PARSER.parse(src)
    b = _RegionBuilder(cfg, path, source)
    class_stack: list[str] = []

    def collect_calls(node, rid: str) -> None:
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type == "call":
                name = _ts_call_name(cur, src)
                if name:
                    b.calls.append((rid, name))
            stack.extend(cur.children)

    def visit(node) -> None:
        t = node.type
        if t == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                # Attribute decorator lines to the definition so edits to either invalidate.
                visit_def(inner, start_row=node.start_point[0])
            return
        if t in {"function_definition", "class_definition"}:
            visit_def(node)
            return
        if t == "import_statement":
            for child in node.named_children:
                if child.type == "dotted_name":
                    b.imports.append((b.module_id, _ts_text(child, src), None))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    alias = child.child_by_field_name("alias")
                    b.imports.append(
                        (b.module_id, _ts_text(name, src) if name else "", _ts_text(alias, src) if alias else None)
                    )
            return
        if t == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            mod = _ts_text(mod_node, src) if mod_node is not None else ""
            for child in node.named_children:
                if child is mod_node:
                    continue
                if child.type == "dotted_name":
                    b.imports.append((b.module_id, mod, _ts_text(child, src)))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        b.imports.append((b.module_id, mod, _ts_text(name, src)))
                elif child.type == "wildcard_import":
                    b.imports.append((b.module_id, mod, "*"))
            return
        for child in node.children:
            visit(child)

    def visit_def(node, start_row: int | None = None) -> None:
        name_node = node.child_by_field_name("name")
        name = _ts_text(name_node, src) if name_node is not None else "<anon>"
        start = (start_row if start_row is not None else node.start_point[0]) + 1
        end = node.end_point[0] + 1
        if node.type == "class_definition":
            q = _qualified(class_stack, name)
            b.add_region(start, end, q, "class")
            class_stack.append(name)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child)
            class_stack.pop()
        else:
            q = _qualified(class_stack, name)
            kind = "method" if class_stack else "function"
            rid = b.add_region(start, end, q, kind)
            collect_calls(node, rid)
            body = node.child_by_field_name("body")
            if body is not None:
                # nested defs are regions too
                saved = list(class_stack)
                for child in body.children:
                    if child.type in {"function_definition", "class_definition", "decorated_definition"}:
                        visit(child)
                class_stack[:] = saved

    visit(tree.root_node)
    return b.finish("tree-sitter")


# --------------------------------------------------------------------------- #
# ast fallback backend
# --------------------------------------------------------------------------- #


def _ast_call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return None


def _parse_with_ast(path: Path, source: str, cfg: LedgerConfig) -> ParsedFile:
    b = _RegionBuilder(cfg, path, source)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return b.finish("ast")
    class_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
            b.add_region(start, node.end_lineno or node.lineno, _qualified(class_stack, node.name), "class")
            class_stack.append(node.name)
            self.generic_visit(node)
            class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._fn(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._fn(node)

        def _fn(self, node) -> None:
            start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
            kind = "method" if class_stack else "function"
            rid = b.add_region(start, node.end_lineno or node.lineno, _qualified(class_stack, node.name), kind)
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    callee = _ast_call_name(child)
                    if callee:
                        b.calls.append((rid, callee))
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                b.imports.append((b.module_id, alias.name, alias.asname))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                b.imports.append((b.module_id, node.module or "", alias.name))

    Visitor().visit(tree)
    return b.finish("ast")


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def parse_python_source(path: Path, source: str, cfg: LedgerConfig) -> ParsedFile:
    if TREE_SITTER_AVAILABLE and cfg.parser_backend != "ast":
        try:
            return _parse_with_treesitter(path, source, cfg)
        except Exception:
            pass
    return _parse_with_ast(path, source, cfg)


def parse_file(path: Path, cfg: LedgerConfig) -> ParsedFile:
    source = path.read_text(encoding="utf-8", errors="replace")
    return parse_python_source(path, source, cfg)
