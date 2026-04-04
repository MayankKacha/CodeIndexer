"""
Tests for the code parsing engine.

Tests language detection, AST parsing, and code splitting across
multiple programming languages.
"""

import tempfile
from pathlib import Path

import pytest

from code_indexer.parsing.language_detector import (
    detect_language,
    get_supported_languages,
    is_supported_file,
)
from code_indexer.parsing.models import CodeElement


# ── Language Detection Tests ────────────────────────────────────────────


class TestLanguageDetection:
    def test_python_detection(self):
        assert detect_language("test.py") == "python"
        assert detect_language("test.pyi") == "python"

    def test_javascript_detection(self):
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"

    def test_typescript_detection(self):
        assert detect_language("service.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"

    def test_java_detection(self):
        assert detect_language("Main.java") == "java"

    def test_go_detection(self):
        assert detect_language("main.go") == "go"

    def test_rust_detection(self):
        assert detect_language("lib.rs") == "rust"

    def test_c_detection(self):
        assert detect_language("main.c") == "c"
        assert detect_language("header.h") == "c"

    def test_cpp_detection(self):
        assert detect_language("main.cpp") == "cpp"
        assert detect_language("class.hpp") == "cpp"

    def test_unsupported(self):
        assert detect_language("readme.md") is None
        assert detect_language("image.png") is None

    def test_is_supported_file(self):
        assert is_supported_file("test.py") is True
        assert is_supported_file("test.txt") is False

    def test_supported_languages(self):
        langs = get_supported_languages()
        assert "python" in langs
        assert "javascript" in langs
        assert "java" in langs
        assert len(langs) >= 10


# ── Code Element Model Tests ───────────────────────────────────────────


class TestCodeElement:
    def test_auto_id_generation(self):
        el = CodeElement(
            name="test_func",
            file_path="test.py",
            repo_name="repo",
            start_line=1,
        )
        assert el.element_id  # Should be auto-generated
        assert len(el.element_id) == 24  # SHA256 hex prefix

    def test_qualified_name_auto(self):
        el = CodeElement(name="method", parent_class="MyClass")
        assert el.qualified_name == "MyClass.method"

    def test_search_text(self):
        el = CodeElement(
            element_type="function",
            name="process_data",
            file_path="utils.py",
            description="Processes input data",
            code="def process_data(x): return x * 2",
        )
        text = el.to_search_text()
        assert "process_data" in text
        assert "Processes input data" in text
        assert "def process_data" in text

    def test_embedding_text(self):
        el = CodeElement(
            element_type="function",
            name="calc",
            description="Calculates result",
            signature="def calc(x, y):",
        )
        text = el.to_embedding_text()
        assert "function: calc" in text
        assert "Calculates result" in text

    def test_context_string(self):
        el = CodeElement(
            element_type="function",
            qualified_name="MyClass.my_method",
            file_path="service.py",
            start_line=10,
            end_line=20,
            description="Does something",
            code="def my_method(self): pass",
        )
        ctx = el.to_context_string()
        assert "MyClass.my_method" in ctx
        assert "service.py" in ctx
        assert "10" in ctx
        assert "Does something" in ctx

    def test_line_count(self):
        el = CodeElement(start_line=10, end_line=25)
        assert el.line_count == 16


# ── AST Parser Tests ───────────────────────────────────────────────────


class TestASTParser:
    def _write_temp_file(self, content: str, suffix: str) -> Path:
        """Write content to a temp file and return its path."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def test_parse_python_functions(self):
        from code_indexer.parsing.ast_parser import parse_file

        code = '''
def hello(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    return a + b
'''
        path = self._write_temp_file(code, ".py")
        elements = parse_file(path, "python", "test-repo")

        assert len(elements) >= 2
        names = [el.name for el in elements]
        assert "hello" in names
        assert "add" in names

        hello = next(el for el in elements if el.name == "hello")
        assert hello.element_type == "function"
        assert hello.start_line > 0
        assert hello.end_line >= hello.start_line

    def test_parse_python_class_with_methods(self):
        from code_indexer.parsing.ast_parser import parse_file

        code = '''
class Calculator:
    """A simple calculator."""

    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b
'''
        path = self._write_temp_file(code, ".py")
        elements = parse_file(path, "python", "test-repo")

        types = {el.element_type for el in elements}
        assert "class" in types
        assert "method" in types

        methods = [el for el in elements if el.element_type == "method"]
        assert len(methods) >= 2
        for m in methods:
            assert m.parent_class == "Calculator"

    def test_parse_javascript_function(self):
        from code_indexer.parsing.ast_parser import parse_file

        code = '''
function greet(name) {
    return "Hello, " + name;
}

class UserService {
    constructor(db) {
        this.db = db;
    }

    getUser(id) {
        return this.db.find(id);
    }
}
'''
        path = self._write_temp_file(code, ".js")
        elements = parse_file(path, "javascript", "test-repo")

        names = [el.name for el in elements]
        assert "greet" in names


# ── Code Splitter Tests ─────────────────────────────────────────────────


class TestCodeSplitter:
    def test_split_codebase(self):
        from code_indexer.parsing.code_splitter import split_codebase

        # Create a temp directory with sample files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Python file
            py_file = Path(tmpdir) / "service.py"
            py_file.write_text('''
class PaymentService:
    def process_payment(self, user, amount):
        """Process a payment."""
        if amount <= 0:
            raise ValueError("Invalid amount")
        return self._charge(user, amount)

    def _charge(self, user, amount):
        return {"status": "ok", "charged": amount}

def validate_input(data):
    return bool(data)
''')
            elements, stats = split_codebase(tmpdir, "test-repo")

            assert len(elements) >= 3  # class + 2 methods + 1 function
            assert stats["total_elements"] >= 3
            assert "python" in stats["languages"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
