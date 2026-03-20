"""
Code Hyperedge Extractor
========================
Uses Python's ast module to extract precise, structured hyperedges from code.
Zero LLM cost. Produces directed edges with sources and targets.

Edge types: CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES, DECORATES.
"""

from __future__ import annotations

import ast
import re
from hashlib import md5
from pathlib import Path

from ..ingestion.chunker import Chunk
from ..models import EdgeType, HyperedgeRecord


# ---------------------------------------------------------------------------
# Python AST visitor — directed edge extraction
# ---------------------------------------------------------------------------

class _PythonHyperedgeVisitor(ast.NodeVisitor):
    """Walks a Python AST and emits HyperedgeRecord objects with directed sources/targets."""

    def __init__(self, module_name: str, chunk_id: str, source_path: str, chunk_text: str):
        self.module_name = module_name
        self.chunk_id = chunk_id
        self.source_path = source_path
        self.chunk_text = chunk_text
        self.edges: list[HyperedgeRecord] = []
        self._current_class: str | None = None
        self._current_func: str | None = None
        self._edge_counter = 0

    def _make_edge_id(self, edge_type: str, key: str) -> str:
        self._edge_counter += 1
        raw = f"{edge_type}_{key}_{self.chunk_id[:8]}_{self._edge_counter}"
        return md5(raw.encode()).hexdigest()[:16]

    def _make_edge(
        self, sources: list[str], targets: list[str],
        relation: str, edge_type: str, **meta,
    ) -> HyperedgeRecord:
        sources = [s for s in sources if s]
        targets = [t for t in targets if t]
        eid = self._make_edge_id(edge_type, relation[:30])
        return HyperedgeRecord(
            edge_id=eid,
            relation=relation,
            edge_type=edge_type,
            sources=sources,
            targets=targets,
            source_path=self.source_path,
            chunk_id=self.chunk_id,
            chunk_text=self.chunk_text,
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
    """Extracts hyperedges from code chunks using AST analysis."""

    def extract(self, chunk: Chunk) -> list[HyperedgeRecord]:
        if chunk.file_type == "py":
            return self._extract_python(chunk)
        else:
            return self._extract_generic_code(chunk)

    def extract_all(self, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        edges: list[HyperedgeRecord] = []
        for chunk in chunks:
            if chunk.is_code:
                edges.extend(self.extract(chunk))
        return edges

    def _extract_python(self, chunk: Chunk) -> list[HyperedgeRecord]:
        source_path = chunk.source_path
        module_name = Path(source_path).stem

        try:
            tree = ast.parse(chunk.text)
        except SyntaxError:
            return self._extract_generic_code(chunk)

        visitor = _PythonHyperedgeVisitor(
            module_name=module_name,
            chunk_id=chunk.chunk_id,
            source_path=source_path,
            chunk_text=chunk.text,
        )
        visitor.visit(tree)

        # Module-level DEFINES edge
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
