"""Lint and test integration for Rooks Code.

Runs configured linters and tests after edits, feeds errors back to the model
with file context and line numbers. Inspired by aider's linter integration.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .tools import SKIP_DIRS, ToolError


@dataclass
class LintResult:
    """Result of a lint check."""
    file: str
    errors: List[str]
    lines: List[int]  # 1-based line numbers with errors


@dataclass
class TestResult:
    """Result of a test run."""
    passed: bool
    output: str
    errors: List[str]
    failed_tests: List[str]


class LintTestRunner:
    """Runs linters and tests for the project."""

    def __init__(self, root: Path, config: Optional[Dict] = None):
        self.root = root.resolve()
        self.config = config or {}

        # Default lint commands per language
        self.lint_commands = {
            "python": self.config.get("python_lint", "python -m py_compile"),
            "python_flake8": self.config.get("python_flake8", "python -m flake8 --select=E9,F821,F823,F831,F406,F407,F701,F702,F704,F706"),
            "javascript": self.config.get("javascript_lint", "eslint"),
            "typescript": self.config.get("typescript_lint", "tsc --noEmit"),
            "rust": self.config.get("rust_lint", "cargo check"),
            "go": self.config.get("go_lint", "go vet"),
        }

        # Default test commands
        self.test_commands = {
            "python": self.config.get("python_test", "python -m pytest -q"),
            "javascript": self.config.get("javascript_test", "npm test"),
            "typescript": self.config.get("typescript_test", "npm test"),
            "rust": self.config.get("rust_test", "cargo test"),
            "go": self.config.get("go_test", "go test ./..."),
        }

    def _detect_language(self, path: Path) -> Optional[str]:
        """Detect the language of a file."""
        suffix = path.suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in (".js", ".jsx", ".mjs"):
            return "javascript"
        if suffix in (".ts", ".tsx"):
            return "typescript"
        if suffix == ".rs":
            return "rust"
        if suffix == ".go":
            return "go"
        return None

    def run_lint(self, file: Path) -> LintResult:
        """Run linter on a single file."""
        rel_file = file.relative_to(self.root).as_posix()
        lang = self._detect_language(file)
        if not lang:
            return LintResult(file=rel_file, errors=[], lines=[])

        errors = []
        lines = []

        # Run primary linter
        cmd = self.lint_commands.get(lang)
        if cmd:
            full_cmd = f"{cmd} {rel_file}"
            try:
                result = subprocess.run(
                    full_cmd,
                    shell=True,
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode != 0:
                    output = result.stdout + result.stderr
                    errors.append(output.strip())
                    # Extract line numbers
                    lines.extend(self._extract_line_numbers(output, rel_file))
            except subprocess.TimeoutExpired:
                errors.append(f"Lint timed out after 60s: {full_cmd}")
            except Exception as e:
                errors.append(f"Lint error: {e}")

        # For Python, also run flake8 for common errors
        if lang == "python":
            flake8_cmd = self.lint_commands.get("python_flake8")
            if flake8_cmd:
                full_cmd = f"{flake8_cmd} {rel_file}"
                try:
                    result = subprocess.run(
                        full_cmd,
                        shell=True,
                        cwd=self.root,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if result.returncode != 0:
                        output = result.stdout + result.stderr
                        errors.append(output.strip())
                        lines.extend(self._extract_line_numbers(output, rel_file))
                except Exception:
                    pass

        return LintResult(file=rel_file, errors=errors, lines=list(set(lines)))

    def _extract_line_numbers(self, output: str, file: str) -> List[int]:
        """Extract line numbers from linter output."""
        lines = []
        # Common patterns: file:line:, file:line:col:, line N
        patterns = [
            rf"{re.escape(file)}:(\d+):",
            rf"{re.escape(file)}:(\d+):\d+:",
            r"line (\d+)",
            r"Line (\d+)",
            r":(\d+):\d+:",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, output):
                try:
                    line_num = int(match.group(1))
                    if line_num > 0:
                        lines.append(line_num)
                except (ValueError, IndexError):
                    pass
        return lines

    def run_tests(self, pattern: Optional[str] = None) -> TestResult:
        """Run tests for the project."""
        # Detect project type
        has_pyproject = (self.root / "pyproject.toml").exists()
        has_setup = (self.root / "setup.py").exists()
        has_package_json = (self.root / "package.json").exists()
        has_cargo = (self.root / "Cargo.toml").exists()
        has_go_mod = (self.root / "go.mod").exists()

        if has_pyproject or has_setup:
            cmd = self.test_commands.get("python", "python -m pytest -q")
        elif has_package_json:
            cmd = self.test_commands.get("javascript", "npm test")
        elif has_cargo:
            cmd = self.test_commands.get("rust", "cargo test")
        elif has_go_mod:
            cmd = self.test_commands.get("go", "go test ./...")
        else:
            cmd = self.test_commands.get("python", "python -m pytest -q")

        if pattern:
            if "pytest" in cmd:
                cmd += f" -k {pattern}"
            elif "jest" in cmd or "vitest" in cmd:
                cmd += f" -t {pattern}"

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout + result.stderr
            passed = result.returncode == 0

            errors = []
            failed_tests = []
            if not passed:
                errors.append(output.strip())
                # Extract failed test names
                failed_tests = self._extract_failed_tests(output)

            return TestResult(
                passed=passed,
                output=output.strip(),
                errors=errors,
                failed_tests=failed_tests,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output="",
                errors=["Tests timed out after 120s"],
                failed_tests=[],
            )
        except Exception as e:
            return TestResult(
                passed=False,
                output="",
                errors=[f"Test error: {e}"],
                failed_tests=[],
            )

    def _extract_failed_tests(self, output: str) -> List[str]:
        """Extract failed test names from test output."""
        failed = []
        # pytest patterns
        for match in re.finditer(r"(FAILED|ERROR)\s+([\w_:.\[\]]+)", output):
            failed.append(match.group(2))
        # Generic patterns
        for match in re.finditer(r"(?:test|Test)\s+(\S+)\s+(?:FAILED|failed|error)", output, re.IGNORECASE):
            failed.append(match.group(1))
        return list(set(failed))[:20]  # Limit to 20

    def run_lint_on_changed_files(self, changed_files: List[Path]) -> List[LintResult]:
        """Run linter on a list of changed files."""
        results = []
        for file in changed_files:
            if file.exists() and file.is_file():
                result = self.run_lint(file)
                if result.errors:
                    results.append(result)
        return results

    def format_lint_results(self, results: List[LintResult]) -> str:
        """Format lint results for the model."""
        if not results:
            return "All lint checks passed."

        lines = ["# Lint errors found:"]
        for result in results:
            lines.append(f"\n## {result.file}")
            for error in result.errors:
                lines.append(error)
            if result.lines:
                lines.append(f"Lines with errors: {', '.join(str(l) for l in sorted(result.lines))}")
        return "\n".join(lines)

    def format_test_result(self, result: TestResult) -> str:
        """Format test result for the model."""
        if result.passed:
            return "All tests passed."

        lines = ["# Test failures:"]
        lines.append(result.output[:5000])  # Truncate long output
        if result.failed_tests:
            lines.append(f"\nFailed tests: {', '.join(result.failed_tests[:10])}")
        return "\n".join(lines)