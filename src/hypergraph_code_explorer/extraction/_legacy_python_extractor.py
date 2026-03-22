"""
LEGACY: Python AST-based Code Hyperedge Extractor
===================================================
This is the original Python-only AST extractor, preserved as a reference.
It is NOT imported anywhere — the tree-sitter backend (treesitter_extractor.py)
has replaced it for all languages including Python.

This file exists only for comparison during validation and can be deleted
once tree-sitter Python extraction is fully validated.

Original description:
Uses Python's ast module to extract precise, structured hyperedges from code.
Zero LLM cost. Produces directed edges with sources and targets.

Edge types: CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES, DECORATES.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from hashlib import md5
from pathlib import Path

from ..ingestion.chunker import Chunk
from ..models import EdgeType, HyperedgeRecord


# ---------------------------------------------------------------------------
# Python AST visitor — directed edge extraction
# ---------------------------------------------------------------------------

class _PythonHyperedgeVisitor(ast.NodeVisitor):
    """Walks a Python AST and emits HyperedgeRecord objects with directed sources/targets."""

    def __init__(self, module_name: str, source_path: str, source_text: str):
        self.module_name = module_name
        self.source_path = source_path
        self.source_text = source_text
        self.edges: list[HyperedgeRecord] = []
        self._current_class: str | None = None
        self._current_func: str | None = None
        self._edge_counter = 0

    def _make_edge_id(self, edge_type: str, key: str) -> str:
        self._edge_counter += 1
        raw = f"{edge_type}_{key}_{self.source_path}_{self._edge_counter}"
        return md5(raw.encode()).hexdigest()[:16]

    def _make_edge(
        self, sources: list[str], targets: list[str],
        relation: str, edge_type: str, line: int = 0, **meta,
    ) -> HyperedgeRecord:
        sources = [s for s in sources if s]
        targets = [t for t in targets if t]
        eid = self._make_edge_id(edge_type, relation[:30])
        meta["line"] = line
        return HyperedgeRecord(
            edge_id=eid,
            relation=relation,
            edge_type=edge_type,
            sources=sources,
            targets=targets,
            source_path=self.source_path,
            chunk_id="",  # filled in later by _associate_chunks
            chunk_text="",  # filled in later
            metadata=meta,
        )

    def _name_of(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            left = self._name_of(node.value)
            return f"{left}.{node.attr}" if left else node.attr
        if isinstance(node, ast.Subscript):
            return self._name_of(node.value)
        if isinstance(node, ast.Call):
            return self._name_of(node.func)
        return None

    def _annotation_str(self, node: ast.expr | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return str(node.value)
        return self._name_of(node) or ast.unparse(node)

    def _qualified(self, name: str) -> str:
        if self._current_class:
            return f"{self._current_class}.{name}"
        return f"{self.module_name}.{name}"

    # ---- visitors ----------------------------------------------------------

    def visit_Import(self, node: ast.Import):
        imported = [alias.asname or alias.name for alias in node.names]
        if imported:
            self.edges.append(self._make_edge(
                sources=[self.module_name],
                targets=imported,
                relation=f"{self.module_name} imports {', '.join(imported)}",
                edge_type=EdgeType.IMPORTS,
                line=node.lineno,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        imported = [alias.asname or alias.name for alias in node.names]
        if imported:
            self.edges.append(self._make_edge(
                sources=[self.module_name],
                targets=[module] + imported if module else imported,
                relation=f"{self.module_name} imports {', '.join(imported)} from {module}",
                edge_type=EdgeType.IMPORTS,
                from_module=module, line=node.lineno,
            ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        class_qname = f"{self.module_name}.{node.name}"
        bases = [b for b in (self._name_of(b) for b in node.bases) if b]

        # INHERITS: source=subclass, target=base classes
        if bases:
            self.edges.append(self._make_edge(
                sources=[class_qname],
                targets=bases,
                relation=f"{node.name} inherits from {', '.join(bases)}",
                edge_type=EdgeType.INHERITS,
                class_name=node.name, line=node.lineno,
            ))

        # DECORATES: source=decorator, target=decorated class
        decs = [d for d in (self._name_of(d) for d in node.decorator_list) if d]
        if decs:
            self.edges.append(self._make_edge(
                sources=decs,
                targets=[class_qname],
                relation=f"{node.name} decorated with {', '.join(decs)}",
                edge_type=EdgeType.DECORATES,
                line=node.lineno,
            ))

        # DEFINES: source=class, target=methods
        methods = [
            f"{class_qname}.{n.name}"
            for n in ast.iter_child_nodes(node)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if methods:
            self.edges.append(self._make_edge(
                sources=[class_qname],
                targets=methods,
                relation=f"{node.name} defines methods: {', '.join(m.split('.')[-1] for m in methods)}",
                edge_type=EdgeType.DEFINES,
                class_name=node.name, line=node.lineno,
            ))

        prev_class = self._current_class
        self._current_class = class_qname
        self.generic_visit(node)
        self._current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        func_qname = self._qualified(node.name)

        # DECORATES: source=decorator, target=function
        decs = [d for d in (self._name_of(d) for d in node.decorator_list) if d]
        if decs:
            self.edges.append(self._make_edge(
                sources=decs,
                targets=[func_qname],
                relation=f"{node.name} decorated with {', '.join(decs)}",
                edge_type=EdgeType.DECORATES,
                line=node.lineno,
            ))

        # SIGNATURE: source=function, target=param types + return type
        annotations = []
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            ann = self._annotation_str(arg.annotation)
            if ann:
                annotations.append(ann)
        ret = self._annotation_str(node.returns)
        if ret:
            annotations.append(ret)
        if annotations:
            self.edges.append(self._make_edge(
                sources=[func_qname],
                targets=annotations,
                relation=f"{node.name} signature involves types: {', '.join(annotations)}",
                edge_type=EdgeType.SIGNATURE,
                line=node.lineno,
            ))

        # CALLS: source=caller, target=callees
        call_names: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._name_of(child.func)
                if name and name != func_qname:
                    call_names.append(name)
        call_names = list(dict.fromkeys(call_names))
        if call_names:
            self.edges.append(self._make_edge(
                sources=[func_qname],
                targets=call_names,
                relation=f"{node.name} calls: {', '.join(call_names)}",
                edge_type=EdgeType.CALLS,
                line=node.lineno,
            ))

        # RAISES: source=function, target=exception types
        raises: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Raise) and child.exc is not None:
                exc_name = self._name_of(child.exc)
                if exc_name:
                    raises.append(exc_name)
        raises = list(dict.fromkeys(raises))
        if raises:
            self.edges.append(self._make_edge(
                sources=[func_qname],
                targets=raises,
                relation=f"{node.name} raises: {', '.join(raises)}",
                edge_type=EdgeType.RAISES,
                line=node.lineno,
            ))

        prev_func = self._current_func
        self._current_func = func_qname
        self.generic_visit(node)
        self._current_func = prev_func


# ---------------------------------------------------------------------------
# Public extractor class
# ---------------------------------------------------------------------------

class CodeHyperedgeExtractor:
    """
    Extracts hyperedges from code chunks using AST analysis.

    For Python files, extraction runs once on the FULL file source to ensure
    correct class qualification and complete DEFINES edges. Edges are then
    associated back to their originating chunks by line range.
    """

    def extract(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """Extract from a single chunk (used for non-Python or single-chunk files)."""
        if chunk.file_type == "py":
            return self._extract_python_chunk(chunk)
        else:
            return self._extract_generic_code(chunk)

    def extract_all(self, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        """
        Extract from all code chunks. Groups Python chunks by file and
        extracts per-file to get correct DEFINES and qualification.
        """
        edges: list[HyperedgeRecord] = []

        # Group Python chunks by source file
        py_chunks_by_file: dict[str, list[Chunk]] = defaultdict(list)
        other_chunks: list[Chunk] = []

        for chunk in chunks:
            if not chunk.is_code:
                continue
            if chunk.file_type == "py":
                py_chunks_by_file[chunk.source_path].append(chunk)
            else:
                other_chunks.append(chunk)

        # Extract Python files: use full-file source from the largest chunk
        for source_path, file_chunks in py_chunks_by_file.items():
            file_edges = self._extract_python_file(source_path, file_chunks)
            edges.extend(file_edges)

        # Extract non-Python chunks individually
        for chunk in other_chunks:
            edges.extend(self._extract_generic_code(chunk))

        return edges

    def _extract_python_file(
        self, source_path: str, chunks: list[Chunk],
    ) -> list[HyperedgeRecord]:
        """
        Extract edges from a Python file using the full source.
        The full source is reconstructed from chunks, with the largest chunk
        (typically the whole-class or whole-file chunk) used as the primary source.
        """
        module_name = Path(source_path).stem

        # Find the full file source: use the largest chunk or concatenate
        # The chunker produces whole-class chunks that contain all methods,
        # plus individual method chunks. The whole-file source gives us
        # correct class context for all methods.
        full_source = self._reconstruct_file_source(source_path, chunks)

        try:
            tree = ast.parse(full_source)
        except SyntaxError:
            # Fall back to per-chunk extraction
            edges: list[HyperedgeRecord] = []
            for chunk in chunks:
                edges.extend(self._extract_python_chunk(chunk))
            return edges

        # Run visitor on full file AST — correct class qualification
        visitor = _PythonHyperedgeVisitor(
            module_name=module_name,
            source_path=source_path,
            source_text=full_source,
        )
        visitor.visit(tree)

        # Module-level DEFINES edge (one per file, listing ALL top-level symbols)
        top_level_names = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top_level_names.append(f"{module_name}.{node.name}")
            elif isinstance(node, ast.ClassDef):
                top_level_names.append(f"{module_name}.{node.name}")
        if top_level_names:
            eid = md5(f"DEFINES_{module_name}_{source_path}".encode()).hexdigest()[:16]
            visitor.edges.insert(0, HyperedgeRecord(
                edge_id=eid,
                relation=f"module {module_name} defines: {', '.join(n.split('.')[-1] for n in top_level_names)}",
                edge_type=EdgeType.DEFINES,
                sources=[module_name],
                targets=top_level_names,
                source_path=source_path,
                chunk_id="",
                chunk_text="",
            ))

        # Associate edges back to chunks by line number
        self._associate_chunks(visitor.edges, chunks)

        return visitor.edges

    def _reconstruct_file_source(
        self, source_path: str, chunks: list[Chunk],
    ) -> str:
        """
        Get the full file source. Try reading from disk first;
        fall back to concatenating non-overlapping chunks.
        """
        try:
            return Path(source_path).read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, OSError):
            pass

        if not chunks:
            return ""

        # Fall back: concatenate non-overlapping chunks (module, class, function
        # level — skip method chunks since they're contained in class chunks).
        # Sort by start_line to maintain order.
        top_chunks = [
            c for c in chunks
            if c.symbol_type in ("module", "class", "function", None)
        ]
        if not top_chunks:
            top_chunks = chunks

        top_chunks.sort(key=lambda c: c.start_line or 0)
        return "\n\n".join(c.text for c in top_chunks)

    def _associate_chunks(
        self, edges: list[HyperedgeRecord], chunks: list[Chunk],
    ) -> None:
        """Associate each edge with the best-matching chunk by line number."""
        if not chunks:
            return

        # Build line-range index for chunks
        chunk_ranges: list[tuple[int, int, Chunk]] = []
        for chunk in chunks:
            start = chunk.start_line or 1
            end = chunk.end_line or 999999
            chunk_ranges.append((start, end, chunk))

        # Default chunk for edges without line info
        default_chunk = chunks[0]

        for edge in edges:
            line = edge.metadata.get("line", 0)
            if line > 0:
                # Find the most specific (smallest) chunk containing this line
                best_chunk = default_chunk
                best_size = float("inf")
                for start, end, chunk in chunk_ranges:
                    if start <= line <= end:
                        size = end - start
                        if size < best_size:
                            best_size = size
                            best_chunk = chunk
                edge.chunk_id = best_chunk.chunk_id
                edge.chunk_text = best_chunk.text
            else:
                edge.chunk_id = default_chunk.chunk_id
                edge.chunk_text = default_chunk.text

    def _extract_python_chunk(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """
        Extract from a single Python chunk. Used as fallback when full-file
        extraction fails, or for single-chunk scenarios (e.g., tests).

        Uses chunk.symbol_name to restore class context for method chunks.
        """
        source_path = chunk.source_path
        module_name = Path(source_path).stem

        try:
            tree = ast.parse(chunk.text)
        except SyntaxError:
            return self._extract_generic_code(chunk)

        visitor = _PythonHyperedgeVisitor(
            module_name=module_name,
            source_path=source_path,
            source_text=chunk.text,
        )

        # Restore class context for method chunks
        if chunk.symbol_type == "method" and chunk.symbol_name and "." in chunk.symbol_name:
            class_name = chunk.symbol_name.rsplit(".", 1)[0]
            visitor._current_class = f"{module_name}.{class_name}"

        visitor.visit(tree)

        # Set chunk info on all edges
        for edge in visitor.edges:
            edge.chunk_id = chunk.chunk_id
            edge.chunk_text = chunk.text

        # Module-level DEFINES edge (limited to this chunk)
        top_level_names = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                top_level_names.append(f"{module_name}.{node.name}")
        if top_level_names:
            eid = md5(f"DEFINES_{module_name}_{chunk.chunk_id[:8]}".encode()).hexdigest()[:16]
            visitor.edges.insert(0, HyperedgeRecord(
                edge_id=eid,
                relation=f"module {module_name} defines: {', '.join(n.split('.')[-1] for n in top_level_names)}",
                edge_type=EdgeType.DEFINES,
                sources=[module_name],
                targets=top_level_names,
                source_path=source_path,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
            ))

        return visitor.edges

    def _extract_generic_code(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """Regex-based extraction for non-Python code."""
        edges: list[HyperedgeRecord] = []
        text = chunk.text
        source_path = chunk.source_path
        module_name = Path(source_path).stem

        func_pattern = re.compile(
            r"(?:function|def|func|fn|void|int|string|bool|auto)\s+(\w+)\s*\(",
            re.MULTILINE,
        )
        defined = [m.group(1) for m in func_pattern.finditer(text)]

        class_pattern = re.compile(r"(?:class|interface|struct|enum)\s+(\w+)", re.MULTILINE)
        classes = [m.group(1) for m in class_pattern.finditer(text)]

        all_symbols = defined + classes
        if all_symbols:
            eid = md5(f"DEFINES_{module_name}_{chunk.chunk_id[:8]}_generic".encode()).hexdigest()[:16]
            edges.append(HyperedgeRecord(
                edge_id=eid,
                relation=f"module {module_name} defines: {', '.join(all_symbols)}",
                edge_type=EdgeType.DEFINES,
                sources=[module_name],
                targets=all_symbols,
                source_path=source_path,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
            ))

        import_pattern = re.compile(
            r"(?:import|require|include|using|from)\s+([\w./\"']+)",
            re.MULTILINE,
        )
        imports = [m.group(1).strip("\"'") for m in import_pattern.finditer(text)]
        if imports:
            eid = md5(f"IMPORTS_{module_name}_{chunk.chunk_id[:8]}_generic".encode()).hexdigest()[:16]
            edges.append(HyperedgeRecord(
                edge_id=eid,
                relation=f"module {module_name} imports: {', '.join(imports)}",
                edge_type=EdgeType.IMPORTS,
                sources=[module_name],
                targets=imports,
                source_path=source_path,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
            ))

        return edges
