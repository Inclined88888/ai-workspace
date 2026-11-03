"""Lightweight Repo Map for Rooks Code.

Extracts symbols (functions, classes, methods) from Python files using the standard library AST module.
Ranks symbols by relevance to the current task context.
Emits a token-budgeted summary so the model can navigate the codebase without reading every file.

Inspired by aider's repomap.py but using only stdlib (no tree-sitter, no diskcache).
"""

from __future__ import annotations

import ast
import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .tools import SKIP_DIRS

MAX_FILES = 400          # Python files parsed for the map, at most
MAX_WALK_SECONDS = 1.5   # the map is a hint: never hold up startup for it


@dataclass
class Symbol:
    """A symbol (function, class, method) in the codebase."""
    name: str
    kind: str  # "function", "class", "method", "async_function"
    file: str  # relative path
    line: int
    end_line: int
    signature: str
    docstring: str = ""


@dataclass
class FileSymbols:
    """All symbols in a single file."""
    file: str
    symbols: List[Symbol] = field(default_factory=list)
    mtime: float = 0
    size: int = 0
    content_hash: str = ""


class RepoMap:
    """Lightweight repository map using AST parsing."""

    def __init__(
        self,
        root: Path,
        max_tokens: int = 2000,
        verbose: bool = False,
    ):
        self.root = root.resolve()
        self.max_tokens = max_tokens
        self.verbose = verbose
        self._file_cache: Dict[str, FileSymbols] = {}
        self._cache_valid: Dict[str, bool] = {}

    def _should_skip(self, path: Path) -> bool:
        """Check if a path should be skipped."""
        for part in path.parts:
            if part in SKIP_DIRS:
                return True
        return False

    def _walk_python_files(self) -> List[Path]:
        """Walk the project and find all Python files."""
        files: list[Path] = []
        root = self.root.resolve()
        # A home folder or filesystem root is not a project: walking it would stall startup (cli-agent-hardening).
        if root in (Path.home().resolve(), Path(root.anchor)):
            return files
        deadline = time.monotonic() + MAX_WALK_SECONDS
        for dirpath, dirnames, filenames in os.walk(self.root):
            if len(files) >= MAX_FILES or time.monotonic() > deadline:
                break
            # Skip directories in SKIP_DIRS
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                if fname.endswith(".py"):
                    fpath = Path(dirpath) / fname
                    if not self._should_skip(fpath.relative_to(self.root)):
                        files.append(fpath)
        return sorted(files)

    def _get_file_hash(self, path: Path) -> str:
        """Get a hash of the file content for cache invalidation."""
        try:
            content = path.read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except OSError:
            return ""

    def _is_cache_valid(self, rel_path: str) -> bool:
        """Check if cached symbols for a file are still valid."""
        if rel_path not in self._file_cache:
            return False
        cached = self._file_cache[rel_path]
        try:
            current_mtime = os.path.getmtime(self.root / rel_path)
            current_hash = self._get_file_hash(self.root / rel_path)
            return cached.mtime == current_mtime and cached.content_hash == current_hash
        except OSError:
            return False

    def _parse_file(self, path: Path) -> FileSymbols:
        """Parse a Python file and extract symbols."""
        rel_path = path.relative_to(self.root).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return FileSymbols(file=rel_path)

        mtime = os.path.getmtime(path)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        # Check cache
        if rel_path in self._file_cache:
            cached = self._file_cache[rel_path]
            if cached.mtime == mtime and cached.content_hash == content_hash:
                return cached

        # Parse with AST
        symbols = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            # File has syntax errors, return empty
            return FileSymbols(file=rel_path, mtime=mtime, size=len(content), content_hash=content_hash)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Get function signature
                args = []
                for arg in node.args.args:
                    arg_str = arg.arg
                    if arg.annotation:
                        arg_str += f": {ast.unparse(arg.annotation)}"
                    args.append(arg_str)
                if node.args.vararg:
                    args.append(f"*{node.args.vararg.arg}")
                if node.args.kwarg:
                    args.append(f"**{node.args.kwarg.arg}")
                sig = f"def {node.name}({', '.join(args)})"

                docstring = ast.get_docstring(node) or ""
                if docstring:
                    docstring = docstring.strip().split("\n")[0][:100]

                symbols.append(Symbol(
                    name=node.name,
                    kind="async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                    file=rel_path,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
                    docstring=docstring,
                ))

            elif isinstance(node, ast.ClassDef):
                # Get class signature
                bases = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    else:
                        bases.append(ast.unparse(base))
                sig = f"class {node.name}"
                if bases:
                    sig += f"({', '.join(bases)})"

                docstring = ast.get_docstring(node) or ""
                if docstring:
                    docstring = docstring.strip().split("\n")[0][:100]

                symbols.append(Symbol(
                    name=node.name,
                    kind="class",
                    file=rel_path,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
                    docstring=docstring,
                ))

        file_symbols = FileSymbols(
            file=rel_path,
            symbols=symbols,
            mtime=mtime,
            size=len(content),
            content_hash=content_hash,
        )
        self._file_cache[rel_path] = file_symbols
        return file_symbols

    def get_all_symbols(self) -> List[Symbol]:
        """Get all symbols from all Python files in the project."""
        all_symbols = []
        for path in self._walk_python_files():
            file_symbols = self._parse_file(path)
            all_symbols.extend(file_symbols.symbols)
        return all_symbols

    def get_repo_map(
        self,
        chat_files: Optional[Set[str]] = None,
        mentioned_idents: Optional[Set[str]] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a token-budgeted repository map.

        Args:
            chat_files: Files currently in the conversation (higher priority)
            mentioned_idents: Identifiers mentioned in the conversation (higher priority)
            max_tokens: Override the default token budget

        Returns:
            A formatted string listing files and their symbols, ranked by relevance.
        """
        if max_tokens is None:
            max_tokens = self.max_tokens

        if max_tokens <= 0:
            return ""

        chat_files = chat_files or set()
        mentioned_idents = mentioned_idents or set()

        all_symbols = self.get_all_symbols()

        # Score each symbol for relevance
        scored_symbols = []
        for sym in all_symbols:
            score = 1.0

            # Boost if file is in chat
            if sym.file in chat_files:
                score += 10.0

            # Boost if symbol name matches mentioned identifiers
            sym_name_lower = sym.name.lower()
            for ident in mentioned_idents:
                if ident.lower() in sym_name_lower or sym_name_lower in ident.lower():
                    score += 5.0
                    break

            # Boost classes slightly (they're more structural)
            if sym.kind == "class":
                score += 2.0

            scored_symbols.append((score, sym))

        # Sort by score descending
        scored_symbols.sort(key=lambda x: -x[0])

        # Build output within token budget
        # Rough token estimate: 4 chars per token
        char_budget = max_tokens * 4
        output_lines = []
        current_chars = 0

        # Group by file
        file_to_symbols: Dict[str, List[Symbol]] = {}
        for _, sym in scored_symbols:
            file_to_symbols.setdefault(sym.file, []).append(sym)

        for file, symbols in file_to_symbols.items():
            # File header
            file_header = f"{file}:"
            if current_chars + len(file_header) > char_budget:
                break

            output_lines.append(file_header)
            current_chars += len(file_header) + 1

            # Symbols in this file (limit per file)
            for sym in symbols[:20]:  # Max 20 symbols per file
                kind_marker = {"class": "C", "function": "F", "async_function": "A", "method": "M"}.get(sym.kind, "?")
                line = f"  {kind_marker} {sym.signature}"
                if sym.docstring:
                    line += f"  # {sym.docstring}"

                if current_chars + len(line) > char_budget:
                    output_lines.append("  ...")
                    break

                output_lines.append(line)
                current_chars += len(line) + 1

        return "\n".join(output_lines)

    def get_file_symbols(self, rel_path: str) -> List[Symbol]:
        """Get symbols for a specific file."""
        path = self.root / rel_path
        if not path.exists():
            return []
        return self._parse_file(path).symbols

    def search_symbols(self, query: str, limit: int = 50) -> List[Symbol]:
        """Search for symbols matching a query."""
        query_lower = query.lower()
        all_symbols = self.get_all_symbols()
        matches = []
        for sym in all_symbols:
            if query_lower in sym.name.lower() or query_lower in sym.signature.lower():
                matches.append(sym)
                if len(matches) >= limit:
                    break
        return matches


def build_repo_map_for_context(
    root: Path,
    chat_files: Optional[Set[str]] = None,
    mentioned_idents: Optional[Set[str]] = None,
    max_tokens: int = 2000,
) -> str:
    """Convenience function to build a repo map for the current context."""
    repo_map = RepoMap(root, max_tokens=max_tokens)
    return repo_map.get_repo_map(chat_files=chat_files, mentioned_idents=mentioned_idents)