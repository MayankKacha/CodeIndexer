"""
AST parser using tree-sitter for multi-language code analysis.

Extracts classes, functions, methods, imports, calls, and structural
relationships from source code files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from tree_sitter import Node, Parser

from code_indexer.parsing.models import CodeElement

logger = logging.getLogger(__name__)

# ── Tree-sitter queries per language ────────────────────────────────────
# Each language has different node types for the same concepts.

LANGUAGE_QUERIES: dict[str, dict[str, list[str] | str]] = {
    "python": {
        "function": ["function_definition"],
        "class": ["class_definition"],
        "import": ["import_statement", "import_from_statement"],
        "call": ["call"],
        "decorator": ["decorator"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "return_type_field": "return_type",
        "superclass_field": "superclasses",
        "docstring_node": "expression_statement",
    },
    "javascript": {
        "function": [
            "function_declaration",
            "arrow_function",
            "method_definition",
            "function",
        ],
        "class": ["class_declaration"],
        "import": ["import_statement"],
        "call": ["call_expression"],
        "decorator": ["decorator"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "formal_parameters_field": "formal_parameters",
    },
    "typescript": {
        "function": [
            "function_declaration",
            "arrow_function",
            "method_definition",
            "function",
        ],
        "class": ["class_declaration"],
        "import": ["import_statement"],
        "call": ["call_expression"],
        "decorator": ["decorator"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "formal_parameters_field": "formal_parameters",
        "return_type_field": "return_type",
    },
    "java": {
        "function": ["method_declaration", "constructor_declaration"],
        "class": ["class_declaration", "interface_declaration", "enum_declaration"],
        "import": ["import_declaration"],
        "call": ["method_invocation"],
        "decorator": ["marker_annotation", "annotation"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "formal_parameters_field": "formal_parameters",
        "return_type_field": "type",
        "superclass_field": "superclass",
    },
    "go": {
        "function": ["function_declaration", "method_declaration"],
        "class": ["type_declaration"],
        "import": ["import_declaration"],
        "call": ["call_expression"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "return_type_field": "result",
    },
    "rust": {
        "function": ["function_item"],
        "class": ["struct_item", "enum_item", "impl_item", "trait_item"],
        "import": ["use_declaration"],
        "call": ["call_expression"],
        "decorator": ["attribute_item"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "return_type_field": "return_type",
    },
    "c": {
        "function": ["function_definition"],
        "class": ["struct_specifier", "enum_specifier", "union_specifier"],
        "import": ["preproc_include"],
        "call": ["call_expression"],
        "name_field": "declarator",
        "body_field": "body",
        "parameters_field": "parameters",
    },
    "cpp": {
        "function": ["function_definition"],
        "class": [
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
        ],
        "import": ["preproc_include", "using_declaration"],
        "call": ["call_expression"],
        "name_field": "declarator",
        "body_field": "body",
        "parameters_field": "parameters",
    },
    "c_sharp": {
        "function": ["method_declaration", "constructor_declaration"],
        "class": [
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "enum_declaration",
        ],
        "import": ["using_directive"],
        "call": ["invocation_expression"],
        "decorator": ["attribute"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "return_type_field": "type",
    },
    "ruby": {
        "function": ["method"],
        "class": ["class", "module"],
        "import": ["call"],  # require/include
        "call": ["call", "method_call"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
    },
    "php": {
        "function": ["function_definition", "method_declaration"],
        "class": ["class_declaration", "interface_declaration", "trait_declaration"],
        "import": ["namespace_use_declaration"],
        "call": ["function_call_expression", "method_call_expression"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
        "formal_parameters_field": "formal_parameters",
    },
    "kotlin": {
        "function": ["function_declaration"],
        "class": ["class_declaration", "object_declaration", "interface_declaration"],
        "import": ["import_header"],
        "call": ["call_expression"],
        "decorator": ["annotation"],
        "name_field": "simple_identifier",
        "body_field": "function_body",
        "parameters_field": "function_value_parameters",
    },
    "swift": {
        "function": ["function_declaration", "init_declaration"],
        "class": [
            "class_declaration",
            "struct_declaration",
            "protocol_declaration",
            "enum_declaration",
        ],
        "import": ["import_declaration"],
        "call": ["call_expression"],
        "decorator": ["attribute"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameter_clause",
    },
    "scala": {
        "function": ["function_definition", "val_definition"],
        "class": [
            "class_definition",
            "object_definition",
            "trait_definition",
        ],
        "import": ["import_declaration"],
        "call": ["call_expression"],
        "decorator": ["annotation"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
    },
    "lua": {
        "function": ["function_declaration", "local_function"],
        "class": [],  # Lua uses tables, no native classes
        "import": [],  # require() is a call, not a statement
        "call": ["function_call"],
        "name_field": "name",
        "body_field": "body",
        "parameters_field": "parameters",
    },
    "bash": {
        "function": ["function_definition"],
        "class": [],
        "import": ["command"],  # source/. commands
        "call": ["command"],
        "name_field": "name",
        "body_field": "body",
    },
}


def _get_parser(language: str) -> Parser:
    """Create a tree-sitter parser for the given language."""
    from tree_sitter_language_pack import get_language

    lang = get_language(language)
    parser = Parser(lang)
    return parser


def _get_node_text(node: Node, source_bytes: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _find_name(node: Node, language_config: dict, source_bytes: bytes) -> str:
    """Extract the name of a code element from its AST node."""
    name_field = language_config.get("name_field", "name")

    # Try named child with the field name
    name_node = node.child_by_field_name(name_field)
    if name_node:
        text = _get_node_text(name_node, source_bytes)
        # For C/C++ declarators like "int foo(int x)", extract just "foo"
        if "(" in text:
            text = text.split("(")[0].strip()
        if "*" in text:
            text = text.replace("*", "").strip()
        return text

    # Fallback: look for an identifier child directly
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child, source_bytes)
        if child.type == "simple_identifier":
            return _get_node_text(child, source_bytes)

    # Last resort: first word of the node
    text = _get_node_text(node, source_bytes).strip()
    if text:
        return text.split()[0][:50]
    return "<anonymous>"


def _find_parameters(
    node: Node, language_config: dict, source_bytes: bytes
) -> List[str]:
    """Extract parameter names from a function/method node."""
    params = []
    param_fields = [
        "parameters",
        "formal_parameters",
        "function_value_parameters",
        "parameter_clause",
    ]

    for field_name in param_fields:
        if field_name in language_config:
            param_node = node.child_by_field_name(language_config[field_name])
            if param_node is None:
                param_node = node.child_by_field_name(field_name)
            if param_node:
                for child in param_node.children:
                    if child.type in (
                        "identifier",
                        "typed_parameter",
                        "typed_default_parameter",
                        "default_parameter",
                        "formal_parameter",
                        "simple_parameter",
                        "parameter",
                    ):
                        name = child.child_by_field_name("name")
                        if name:
                            params.append(_get_node_text(name, source_bytes))
                        else:
                            # Fallback: first identifier child
                            for sub in child.children:
                                if sub.type == "identifier":
                                    params.append(_get_node_text(sub, source_bytes))
                                    break
                            else:
                                text = _get_node_text(child, source_bytes).strip()
                                if text and text not in (
                                    "(",
                                    ")",
                                    ",",
                                    "self",
                                    "this",
                                    "cls",
                                ):
                                    params.append(
                                        text.split(":")[0].split("=")[0].strip()
                                    )
                break
    return params


def _find_return_type(node: Node, language_config: dict, source_bytes: bytes) -> str:
    """Extract return type annotation from a function/method node."""
    rt_field = language_config.get("return_type_field", "return_type")
    rt_node = node.child_by_field_name(rt_field)
    if rt_node:
        return _get_node_text(rt_node, source_bytes).strip()
    return ""


def _find_docstring(node: Node, language: str, source_bytes: bytes) -> str:
    """Extract docstring from a function/class body."""
    if language == "python":
        body = node.child_by_field_name("body")
        if body and body.child_count > 0:
            first_stmt = body.children[0]
            if first_stmt.type == "expression_statement":
                child = first_stmt.children[0] if first_stmt.child_count > 0 else None
                if child and child.type == "string":
                    raw = _get_node_text(child, source_bytes)
                    return raw.strip().strip("\"'").strip()
    elif language in ("javascript", "typescript", "java", "c_sharp", "kotlin", "swift"):
        # Look for comment immediately before the node
        prev = node.prev_named_sibling
        if prev and prev.type in ("comment", "block_comment", "line_comment"):
            return (
                _get_node_text(prev, source_bytes)
                .strip()
                .strip("/*")
                .strip("//")
                .strip()
            )
    return ""


def _find_decorators(
    node: Node, language_config: dict, source_bytes: bytes
) -> List[str]:
    """Extract decorators/annotations from a function/class node."""
    decorators = []
    dec_types = language_config.get("decorator", [])
    if not dec_types:
        return decorators

    # Check previous siblings
    sibling = node.prev_named_sibling
    while sibling and sibling.type in dec_types:
        decorators.append(_get_node_text(sibling, source_bytes).strip())
        sibling = sibling.prev_named_sibling

    # Also check children (some languages embed decorators inside)
    for child in node.children:
        if child.type in dec_types:
            decorators.append(_get_node_text(child, source_bytes).strip())

    return decorators


def _find_calls(node: Node, language_config: dict, source_bytes: bytes) -> List[str]:
    """Iteratively find all function/method calls within a node."""
    calls = []
    call_types = language_config.get("call", [])
    stack = [node]

    while stack:
        n = stack.pop()
        if n.type in call_types:
            # Extract the function name from the call
            func_node = n.child_by_field_name("function")
            if func_node:
                calls.append(_get_node_text(func_node, source_bytes).strip())
            else:
                # Try first named child
                for child in n.children:
                    if child.type in (
                        "identifier",
                        "member_expression",
                        "attribute",
                        "field_expression",
                        "method_call",
                        "scoped_identifier",
                    ):
                        calls.append(_get_node_text(child, source_bytes).strip())
                        break
        for child in reversed(n.children):
            stack.append(child)

    return list(set(calls))


def _find_superclasses(
    node: Node, language_config: dict, source_bytes: bytes
) -> List[str]:
    """Extract parent classes / superclasses from a class definition node."""
    superclasses = []
    sc_field = language_config.get("superclass_field")

    if sc_field:
        sc_node = node.child_by_field_name(sc_field)
        if sc_node:
            # Could be an argument_list or comma-separated identifiers
            for child in sc_node.children:
                if child.type in (
                    "identifier",
                    "attribute",
                    "type_identifier",
                    "generic_type",
                    "scoped_type_identifier",
                ):
                    superclasses.append(_get_node_text(child, source_bytes).strip())

    # Also check for "extends" / "implements" keywords in children
    for child in node.children:
        if child.type in (
            "superclass",
            "extends_clause",
            "implements_clause",
            "superclasses",
            "class_heritage",
        ):
            for sub in child.children:
                if sub.type in (
                    "identifier",
                    "type_identifier",
                    "attribute",
                    "generic_type",
                ):
                    superclasses.append(_get_node_text(sub, source_bytes).strip())

    return superclasses


def _estimate_complexity(node: Node, source_bytes: bytes) -> int:
    """Estimate cyclomatic complexity of a code block.

    Counts decision points: if, for, while, case, catch, &&, ||, etc.
    """
    complexity = 1  # Base complexity

    decision_types = {
        "if_statement",
        "if_expression",
        "elif_clause",
        "else_clause",
        "for_statement",
        "for_expression",
        "for_in_statement",
        "while_statement",
        "while_expression",
        "case_clause",
        "case_statement",
        "switch_case",
        "catch_clause",
        "except_clause",
        "rescue",
        "conditional_expression",
        "ternary_expression",
        "match_statement",
        "match_arm",
    }

    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in decision_types:
            complexity += 1
        # Count logical operators
        if n.type in ("binary_expression", "boolean_operator"):
            text = _get_node_text(n, source_bytes)
            complexity += (
                text.count("&&")
                + text.count("||")
                + text.count(" and ")
                + text.count(" or ")
            )
        for child in reversed(n.children):
            stack.append(child)

    return complexity


def _extract_signature(node: Node, language: str, source_bytes: bytes) -> str:
    """Extract just the signature line of a function/method."""
    full_text = _get_node_text(node, source_bytes)
    lines = full_text.split("\n")
    if not lines:
        return ""

    if language == "python":
        # Everything up to the colon
        sig_lines = []
        for line in lines:
            sig_lines.append(line)
            if line.rstrip().endswith(":"):
                break
        return "\n".join(sig_lines)
    elif language in ("java", "c_sharp", "kotlin", "swift", "scala"):
        # Everything up to the opening brace
        sig_lines = []
        for line in lines:
            sig_lines.append(line)
            if "{" in line:
                sig_lines[-1] = line.split("{")[0].strip()
                break
        return "\n".join(sig_lines)
    else:
        # Default: first line
        return lines[0]


def parse_file(
    file_path: str | Path,
    language: str,
    repo_name: str = "",
) -> List[CodeElement]:
    """Parse a source file and extract all code elements.

    Args:
        file_path: Path to the source file.
        language: tree-sitter language name.
        repo_name: Name of the repository.

    Returns:
        List of CodeElement objects found in the file.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return []

    try:
        source_bytes = file_path.read_bytes()
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return []

    if language not in LANGUAGE_QUERIES:
        logger.warning(f"No query config for language: {language}")
        return []

    lang_config = LANGUAGE_QUERIES[language]

    try:
        parser = _get_parser(language)
    except Exception as e:
        logger.error(f"Failed to create parser for {language}: {e}")
        return []

    try:
        tree = parser.parse(source_bytes)
    except Exception as e:
        logger.error(f"Failed to parse {file_path}: {e}")
        return []

    root = tree.root_node
    elements: List[CodeElement] = []
    relative_path = str(file_path)

    # ── Extract top-level imports ───────────────────────────────────────
    file_imports: List[str] = []
    import_types = lang_config.get("import", [])
    for child in root.children:
        if child.type in import_types:
            file_imports.append(_get_node_text(child, source_bytes).strip())

    # ── Walk the AST ────────────────────────────────────────────────────
    function_types = set(lang_config.get("function", []))
    class_types = set(lang_config.get("class", []))

    # ── Iteratively process AST nodes ──────────────────────────────────
    stack = [(root, None, None)]
    while stack:
        node, parent_class_name, parent_element_id = stack.pop()

        # ── Classes ─────────────────────────────────────────────────────
        if node.type in class_types:
            name = _find_name(node, lang_config, source_bytes)
            code = _get_node_text(node, source_bytes)
            docstring = _find_docstring(node, language, source_bytes)
            decorators = _find_decorators(node, lang_config, source_bytes)
            superclasses = _find_superclasses(node, lang_config, source_bytes)

            cls_element = CodeElement(
                element_type="class",
                name=name,
                file_path=relative_path,
                repo_name=repo_name,
                language=language,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                code=code,
                signature=f"class {name}",
                docstring=docstring,
                decorators=decorators,
                imports=file_imports,
                inherits_from=superclasses,
                calls=[],  # Will be populated for methods
            )
            elements.append(cls_element)

            # Process children of the class (methods etc.)
            body = node.child_by_field_name("body") or node.child_by_field_name(
                "class_body"
            )
            if body:
                for child in reversed(body.children):
                    stack.append((child, name, cls_element.element_id))
            else:
                for child in reversed(node.children):
                    stack.append((child, name, cls_element.element_id))
            continue

        # ── Functions / Methods ─────────────────────────────────────────
        if node.type in function_types:
            name = _find_name(node, lang_config, source_bytes)
            code = _get_node_text(node, source_bytes)
            signature = _extract_signature(node, language, source_bytes)
            docstring = _find_docstring(node, language, source_bytes)
            parameters = _find_parameters(node, lang_config, source_bytes)
            return_type = _find_return_type(node, lang_config, source_bytes)
            decorators = _find_decorators(node, lang_config, source_bytes)
            calls = _find_calls(node, lang_config, source_bytes)
            complexity = _estimate_complexity(node, source_bytes)

            element_type = "method" if parent_class_name else "function"

            func_element = CodeElement(
                element_type=element_type,
                name=name,
                file_path=relative_path,
                repo_name=repo_name,
                language=language,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                code=code,
                signature=signature,
                docstring=docstring,
                parameters=parameters,
                return_type=return_type,
                decorators=decorators,
                parent_class=parent_class_name,
                parent_element_id=parent_element_id,
                imports=file_imports,
                calls=calls,
                complexity=complexity,
            )
            elements.append(func_element)

            # Don't recurse into nested functions for simplicity
            continue

        # ── Recurse into other nodes ────────────────────────────────────
        for child in reversed(node.children):
            stack.append((child, parent_class_name, parent_element_id))

    return elements
