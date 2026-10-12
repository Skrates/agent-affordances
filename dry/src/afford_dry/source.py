"""Four wheel-bundled grammars, complete source spans, explicit diagnostics."""

import ast
import tokenize
from functools import lru_cache
from pathlib import Path

import tree_sitter_go
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Tree

from .models import Diagnostic, Entry, Form
from .repository import Repository, language_of
from .shape import SKIP, fingerprints, normalize, text

TS_FUNCTIONS = {
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
}
TS_PARENTS = {"variable_declarator", "public_field_definition", "property_definition"}


@lru_cache
def parser_for(grammar: str) -> Parser:
    factories = {
        "python": tree_sitter_python.language,
        "go": tree_sitter_go.language,
        "rust": tree_sitter_rust.language,
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
    }
    return Parser(Language(factories[grammar]()))


def parse_source(source: bytes, grammar: str) -> Tree:
    parser = parser_for(grammar)
    tree = parser.parse(source)
    # Python 3.13 type-parameter defaults are also newer than the wheel's
    # grammar. The native parser validates them and locates only those defaults.
    # They are type metadata, not executable operations. Preserve all offsets.
    if grammar == "python" and tree.root_node.has_error:
        try:
            native = ast.parse(source.decode("utf-8"))
        except SyntaxError:
            return tree
        offsets = [0]
        for line in source.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        patched = bytearray(source)
        changed = False
        for parameter in ast.walk(native):
            if not isinstance(parameter, (ast.TypeVar, ast.TypeVarTuple, ast.ParamSpec)):
                continue
            default = parameter.default_value
            if default is None or default.end_lineno is None or default.end_col_offset is None:
                continue
            start = offsets[parameter.lineno - 1] + parameter.col_offset
            value_start = offsets[default.lineno - 1] + default.col_offset
            end = offsets[default.end_lineno - 1] + default.end_col_offset
            equals = source.rfind(b"=", start, value_start)
            if equals != -1:
                for index in range(equals, end):
                    if patched[index] not in (10, 13):
                        patched[index] = 32
                changed = True
        if changed:
            tree = parser.parse(bytes(patched))
    # The published TS grammar predates `export type *`. It recovers the
    # export_statement with exactly its `type` modifier marked ERROR. Erasing
    # that non-runtime modifier preserves every byte/line offset and leaves
    # function normalization unchanged. Never rewrite strings or comments.
    if grammar in {"typescript", "tsx"} and tree.root_node.has_error:
        patched = bytearray(source)
        changed = False
        for node in tree.root_node.children:
            if node.type != "export_statement" or not any(
                c.type in {"*", "namespace_export"} for c in node.children
            ):
                continue
            for child in node.children:
                if child.type == "ERROR" and child.text == b"type":
                    patched[child.start_byte : child.end_byte] = b" " * (
                        child.end_byte - child.start_byte
                    )
                    changed = True
        if changed:
            tree = parser.parse(bytes(patched))
    return tree


def ancestors(node: Node) -> list[Node]:
    result = []
    parent = node.parent
    while parent is not None:
        result.append(parent)
        parent = parent.parent
    return result


def functions(root: Node, language: str) -> list[Node]:
    found = []
    stack = [root]
    while stack:
        node = stack.pop()
        candidate_types = TS_FUNCTIONS | {"function_definition", "function_item"}
        parents = ancestors(node) if node.type in candidate_types else []
        if language == "python" and node.type == "function_definition":
            for parent in parents:
                if parent.type == "class_definition":
                    found.append(node)
                    break
                if parent.type == "function_definition":
                    break
            else:
                found.append(node)
        elif language == "go" and node.type in {"function_declaration", "method_declaration"}:
            if any(c.type == "block" for c in node.children):
                found.append(node)
        elif language == "rust" and node.type == "function_item":
            in_tests = any(
                p.type == "mod_item"
                and (name := p.child_by_field_name("name")) is not None
                and text(name) == "tests"
                for p in parents
            )
            if (node.parent is None or node.parent.type != "block") and not in_tests:
                found.append(node)
        elif language == "typescript" and not any(p.type in TS_FUNCTIONS for p in parents):
            if node.type in {"function_declaration", "method_definition"} and any(
                c.type == "statement_block" for c in node.children
            ):
                found.append(node)
            elif (
                node.type in {"arrow_function", "function_expression"}
                and node.parent is not None
                and node.parent.type in TS_PARENTS
            ):
                found.append(node.parent)
        stack.extend(reversed(node.children))
    return found


def inert(statement: Node) -> bool:
    if statement.type in SKIP or statement.type == "pass_statement":
        return True
    if statement.type == "expression_statement":
        return all(
            child.type == "ellipsis"
            or (
                child.type == "string"
                and not any(c.type == "interpolation" for c in child.children)
            )
            for child in statement.named_children
        )
    if statement.type == "raise_statement" and statement.named_children:
        raised = statement.named_children[0]
        if raised.type == "call":
            raised = raised.child_by_field_name("function") or raised
        return text(raised) == "NotImplementedError"
    return False


def declaration_only(node: Node) -> bool:
    """A body of docstrings, `pass`, `...`, or `raise NotImplementedError` executes nothing.

    Normalization drops names, types, and literals, so such a form reduces to its
    parameter-list shape and matches unrelated signatures at high scores.
    """
    function = node.child_by_field_name("value") if node.type in TS_PARENTS else node
    body = function.child_by_field_name("body") if function is not None else None
    return (
        body is not None
        and body.type in {"block", "statement_block"}
        and all(inert(statement) for statement in body.named_children)
    )


def end_line(node: Node) -> int:
    return max(node.start_point.row + 1, node.end_point.row + bool(node.end_point.column))


def symbol(node: Node) -> str:
    name = node.child_by_field_name("name")
    names = [text(name) if name is not None else "<anonymous>"]
    for parent in ancestors(node):
        if parent.type in {"class_definition", "class_declaration", "interface_declaration"}:
            owner = parent.child_by_field_name("name")
            if owner is not None:
                names.insert(0, text(owner))
    return ".".join(names)


def read_forms(
    repo: Repository, path: Path, min_lines: int, min_nodes: int
) -> tuple[list[Form], Diagnostic | None]:
    relative = path.relative_to(repo.root).as_posix()
    language = language_of(path)
    if language is None:
        return [], None
    try:
        if not path.resolve().is_relative_to(repo.root):
            raise ValueError("symlink points outside repository; skipped")
        if language == "python":
            with tokenize.open(path) as handle:
                source = handle.read()
        else:
            source = path.read_text(encoding="utf-8")
        tree = parse_source(source.encode(), "tsx" if path.suffix == ".tsx" else language)
        if tree.root_node.has_error:
            raise ValueError("parser could not read syntax; file excluded from comparison")
        forms = []
        for node in functions(tree.root_node, language):
            start, end = node.start_point.row + 1, end_line(node)
            if end - start + 1 < min_lines or declaration_only(node):
                continue
            if node.parent is not None and node.parent.type == "decorated_definition":
                start = node.parent.start_point.row + 1
            shape = normalize(node)
            if shape is None:
                continue
            count, prints = fingerprints(shape)
            if count >= min_nodes:
                forms.append(
                    Form(
                        repo.name,
                        path,
                        symbol(node),
                        Entry(language, relative, start, end, count, prints),
                    )
                )
        return forms, None
    except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
        return [], Diagnostic(repo=repo.name, file=relative, message=str(exc))
