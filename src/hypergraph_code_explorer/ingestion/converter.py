"""
Document Converter
==================
Uses markitdown to convert any supported document into clean markdown text.
Walks directories, skipping hidden/build directories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConvertedDocument:
    """Holds the output of a document conversion."""
    source_path: str
    markdown: str
    file_type: str
    is_code: bool = False
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# File type classification
# ---------------------------------------------------------------------------

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cs", ".cpp", ".cc", ".cxx", ".c",
    ".h", ".hpp", ".hxx", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".r", ".m", ".lua", ".zig",
}

DIRECT_MARKDOWN_EXTENSIONS = {".md", ".txt", ".rst"}

MARKITDOWN_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".html", ".htm",
    ".xml", ".json", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".eggs", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".tox", ".hce_cache", ".ruff_cache",
}


def classify_file(path: str | Path) -> tuple[str, bool]:
    """Returns (file_type, is_code)."""
    ext = Path(path).suffix.lower()
    is_code = ext in CODE_EXTENSIONS
    file_type = ext.lstrip(".")
    return file_type, is_code


# ---------------------------------------------------------------------------
# Main converter class
# ---------------------------------------------------------------------------

class DocumentConverter:
    """Converts documents to markdown using markitdown."""

    def __init__(
        self,
        llm_client=None,
        llm_model: str = "claude-sonnet-4-6",
        verbose: bool = False,
    ):
        self.llm_client = llm_client
        self.llm_model = llm_model
        self.verbose = verbose
        self._md_converter = None

    def _get_converter(self):
        if self._md_converter is None:
            from markitdown import MarkItDown
            if self.llm_client is not None:
                self._md_converter = MarkItDown(
                    llm_client=self.llm_client,
                    llm_model=self.llm_model,
                )
            else:
                self._md_converter = MarkItDown()
        return self._md_converter

    def convert_file(self, path: str | Path) -> ConvertedDocument:
        """Convert a single file to a ConvertedDocument."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        file_type, is_code = classify_file(path)
        ext = path.suffix.lower()

        if self.verbose:
            print(f"  Converting: {path.name} (type={file_type}, code={is_code})")

        if is_code or ext in DIRECT_MARKDOWN_EXTENSIONS:
            markdown = path.read_text(encoding="utf-8", errors="replace")
        else:
            converter = self._get_converter()
            result = converter.convert(str(path))
            markdown = result.text_content

        return ConvertedDocument(
            source_path=str(path),
            markdown=markdown,
            file_type=file_type,
            is_code=is_code,
            metadata={
                "filename": path.name,
                "size_bytes": path.stat().st_size,
            },
        )

    def convert_directory(
        self,
        directory: str | Path,
        recursive: bool = True,
        extensions: set[str] | None = None,
    ) -> list[ConvertedDocument]:
        """Convert all supported files in a directory."""
        directory = Path(directory)
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        supported = CODE_EXTENSIONS | DIRECT_MARKDOWN_EXTENSIONS | MARKITDOWN_EXTENSIONS
        if extensions is not None:
            supported = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}

        pattern = "**/*" if recursive else "*"
        results: list[ConvertedDocument] = []
        errors: list[tuple[str, str]] = []

        for file_path in sorted(directory.glob(pattern)):
            if not file_path.is_file():
                continue
            # Skip hidden and build directories
            if any(part in SKIP_DIRS for part in file_path.relative_to(directory).parts):
                continue
            if any(part.startswith(".") and part not in (".", "..") for part in file_path.parts):
                continue
            if file_path.suffix.lower() not in supported:
                continue

            try:
                doc = self.convert_file(file_path)
                results.append(doc)
            except Exception as e:
                errors.append((str(file_path), str(e)))
                if self.verbose:
                    print(f"  ERROR converting {file_path.name}: {e}")

        if self.verbose:
            print(f"Converted {len(results)} files, {len(errors)} errors.")

        return results
