"""Dryer's structural normalization and fingerprints for four languages.

Adapted from Robert C. Martin's dryer at 66ff6d21 (0BSD; see NOTICE).
Call names and operators stay; incidental identifiers and literals drop out.
Postorder walks retain support for deeply nested expressions.
"""

from dataclasses import dataclass
from typing import Literal, cast

from tree_sitter import Node


@dataclass(frozen=True)
class K:
    name: str


type Shape = K | str | list[Shape]
type Mode = Literal["norm", "callee", "path"]
type Job = tuple[Node, Mode, bool]
type Key = tuple[int, Mode, bool]

IDENTIFIERS = {
    "identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
    "package_identifier",
}
LITERALS = {
    "string",
    "string_literal",
    "interpreted_string_literal",
    "raw_string_literal",
    "char_literal",
    "rune_literal",
    "character_literal",
    "number",
    "integer",
    "float",
    "decimal_integer_literal",
    "hex_integer_literal",
    "octal_integer_literal",
    "binary_integer_literal",
    "decimal_floating_point_literal",
    "hex_floating_point_literal",
    "int_literal",
    "float_literal",
    "imaginary_literal",
    "integer_literal",
    "true",
    "false",
    "null",
    "nil",
    "none",
    "undefined",
    "null_literal",
    "boolean_literal",
    "template_string",
    "regex",
    "regex_literal",
}
OPERATORS = set(
    [
        "+",
        "-",
        "*",
        "/",
        "%",
        "**",
        "==",
        "!=",
        "<",
        ">",
        "<=",
        ">=",
        "===",
        "!==",
        "&&",
        "||",
        "&",
        "|",
        "^",
        "<<",
        ">>",
        ">>>",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&=",
        "|=",
        "^=",
        "<<=",
        ">>=",
        ">>>=",
        "!",
        "~",
        "++",
        "--",
        "and",
        "or",
        "not",
        "??",
        "?.",
        "=",
        ":=",
        "=>",
        "?",
    ]
)
CALLS = {
    "call",
    "call_expression",
    "method_invocation",
    "object_creation_expression",
    "new_expression",
}
ATTRIBUTES = {
    "attribute",
    "member_expression",
    "selector_expression",
    "field_access",
    "field_expression",
}
SCOPED = {"scoped_identifier", "scoped_type_identifier", "generic_type"}
SKIP = {"comment", "line_comment", "block_comment", "documentation_comment", "doc_comment"}
ARG_LISTS = {"argument_list", "arguments"}
TYPE_ARGS = {"type_arguments", "type_parameters"}


def text(node: Node) -> str:
    return (node.text or b"").decode()


def call_parts(node: Node) -> tuple[list[Node], list[Node], Node | None]:
    before = []
    args = None
    for child in node.named_children:
        if child.type in ARG_LISTS:
            args = child
            break
        before.append(child)
    return (
        [child for child in before if child.type not in TYPE_ARGS],
        [child for child in before if child.type in TYPE_ARGS],
        args,
    )


def jobs(node: Node, mode: Mode, head: bool) -> list[Job]:
    if mode == "callee":
        if node.type in ATTRIBUTES:
            pending: list[Job] = [(child, "norm", False) for child in node.named_children[:-1]]
            if node.named_children and node.named_children[-1].type not in IDENTIFIERS:
                pending.append((node.named_children[-1], "callee", False))
            return pending
        if node.type in SCOPED:
            return [
                (child, "path" if child.type in SCOPED else "norm", False)
                for child in node.named_children
                if child.type not in IDENTIFIERS
            ]
        return [(node, "norm", True)]
    if mode == "path":
        return [
            (child, "path" if child.type in SCOPED else "norm", False)
            for child in node.named_children
            if child.type not in IDENTIFIERS
        ]
    if node.type == "parenthesized_expression" and len(node.named_children) == 1:
        return [(node.named_children[0], "norm", head)]
    if node.type == "macro_invocation":
        macro = node.child_by_field_name("macro")
        return [(child, "callee" if child == macro else "norm", False) for child in node.children]
    if node.type in CALLS:
        rest, types, args = call_parts(node)
        result: list[Job] = [(child, "norm", False) for child in rest[:-1]]
        if rest:
            result.append((rest[-1], "callee", False))
        result.extend((child, "norm", False) for child in types)
        if args is not None:
            result.append((args, "norm", False))
        return result
    if node.type in ATTRIBUTES:
        attribute_jobs: list[Job] = [(child, "norm", False) for child in node.named_children[:-1]]
        if node.named_children and node.named_children[-1].type not in IDENTIFIERS:
            attribute_jobs.append((node.named_children[-1], "callee" if head else "norm", False))
        return attribute_jobs
    return [(child, "norm", False) for child in node.children]


def normalize(root: Node) -> Shape | None:
    cache: dict[Key, Shape | None] = {}
    stack: list[tuple[Node, Mode, bool, bool]] = [(root, "norm", False, False)]
    while stack:
        node, mode, head, expanded = stack.pop()
        key = (node.id, mode, head)
        if key in cache:
            continue
        if mode == "callee" and node.type in IDENTIFIERS:
            cache[key] = [K("symbol"), text(node)]
            continue
        if mode == "norm":
            if node.type in SKIP or (not node.is_named and node.type not in OPERATORS):
                cache[key] = None
                continue
            if node.type in OPERATORS:
                cache[key] = [K("symbol"), node.type]
                continue
            if node.type in IDENTIFIERS:
                cache[key] = [K("symbol"), text(node)] if head else K("symbol")
                continue
            if node.type in LITERALS and not (
                any(c.type in {"interpolation", "template_substitution"} for c in node.children)
            ):
                cache[key] = K("literal")
                continue
        dependencies = jobs(node, mode, head)
        if not expanded:
            stack.append((node, mode, head, True))
            stack.extend(
                (child, child_mode, child_head, False)
                for child, child_mode, child_head in reversed(dependencies)
            )
            continue
        if mode == "callee" and node.type not in ATTRIBUTES | SCOPED:
            cache[key] = cache[(node.id, "norm", True)]
        elif node.type == "parenthesized_expression" and mode == "norm" and len(dependencies) == 1:
            cache[key] = cache[(dependencies[0][0].id, "norm", head)]
        elif node.type in ATTRIBUTES and mode != "path":
            parts: list[Shape] = [K(node.type)]
            for child in node.named_children[:-1]:
                value = cache[(child.id, "norm", False)]
                if value is not None:
                    parts.append(value)
            if node.named_children:
                name = node.named_children[-1]
                if name.type in IDENTIFIERS:
                    parts.append(
                        [K("symbol"), text(name)] if head or mode == "callee" else K("symbol")
                    )
                else:
                    value = cache[
                        (name.id, "callee" if head or mode == "callee" else "norm", False)
                    ]
                    if value is not None:
                        parts.append(value)
            cache[key] = parts
        elif mode == "path" or (mode == "callee" and node.type in SCOPED):
            path_parts: list[Shape] = [K(node.type)]
            for child in node.named_children:
                if child.type in IDENTIFIERS:
                    path_parts.append([K("symbol"), text(child)])
                else:
                    value = cache[(child.id, "path" if child.type in SCOPED else "norm", False)]
                    if value is not None:
                        path_parts.append(value)
            cache[key] = path_parts
        else:
            parts = [K(node.type)]
            for child, child_mode, child_head in dependencies:
                value = cache[(child.id, child_mode, child_head)]
                if value is not None:
                    parts.append(value)
            cache[key] = parts
    return cache[(root.id, "norm", False)]


def atom(node: K | str) -> str:
    if isinstance(node, K):
        return ":" + node.name
    return '"' + node.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fingerprints(root: Shape) -> tuple[int, frozenset[str]]:
    printed: dict[int, str] = {}
    found: set[str] = set()
    count = 0
    stack: list[tuple[Shape, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if not expanded:
            count += 1
        if isinstance(node, list):
            children = cast(list[Shape], node)
            if not expanded:
                stack.append((node, True))
                stack.extend((child, False) for child in reversed(children))
                continue
            value = (
                "["
                + " ".join(printed[id(c)] if isinstance(c, list) else atom(c) for c in children)
                + "]"
            )
            printed[id(node)] = value
        else:
            value = atom(node)
        found.add(value)
    return count, frozenset(found)
