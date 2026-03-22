"""
Tree-Sitter Code Extractor
===========================
Single extraction backend for all supported languages.
Uses tree-sitter to parse source code and extract structural hyperedges
(CALLS, DEFINES, INHERITS, IMPORTS, RAISES) uniformly across languages.

Supported: Python, JavaScript, TypeScript (including TSX/JSX),
Go, Rust, Java, C, C++, Ruby, PHP.
"""

from __future__ import annotations

from hashlib import md5

from tree_sitter import Language, Parser, Node

from ..ingestion.chunker import Chunk
from ..models import EdgeType, HyperedgeRecord


# ---------------------------------------------------------------------------
# Language registry — lazy-loaded parsers
# ---------------------------------------------------------------------------

LANGUAGE_MAP: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "tsx",
    "jsx": "javascript",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "h": "c",
    "hpp": "cpp",
    "hh": "cpp",
    "rb": "ruby",
    "php": "php",
}

_LANGUAGE_LOADERS: dict[str, object] = {}


def _get_language_obj(lang_name: str) -> Language:
    """Load a tree-sitter Language object by name."""
    if lang_name == "python":
        import tree_sitter_python as mod
        return Language(mod.language())
    elif lang_name == "javascript":
        import tree_sitter_javascript as mod
        return Language(mod.language())
    elif lang_name == "typescript":
        import tree_sitter_typescript as mod
        return Language(mod.language_typescript())
    elif lang_name == "tsx":
        import tree_sitter_typescript as mod
        return Language(mod.language_tsx())
    elif lang_name == "go":
        import tree_sitter_go as mod
        return Language(mod.language())
    elif lang_name == "rust":
        import tree_sitter_rust as mod
        return Language(mod.language())
    elif lang_name == "java":
        import tree_sitter_java as mod
        return Language(mod.language())
    elif lang_name == "c":
        import tree_sitter_c as mod
        return Language(mod.language())
    elif lang_name == "cpp":
        import tree_sitter_cpp as mod
        return Language(mod.language())
    elif lang_name == "ruby":
        import tree_sitter_ruby as mod
        return Language(mod.language())
    elif lang_name == "php":
        import tree_sitter_php as mod
        return Language(mod.language_php())
    else:
        raise ValueError(f"Unknown language: {lang_name}")


_PARSER_CACHE: dict[str, Parser] = {}


def _get_parser(lang_name: str) -> Parser:
    """Get or create a cached Parser for the given language."""
    if lang_name not in _PARSER_CACHE:
        lang_obj = _get_language_obj(lang_name)
        _PARSER_CACHE[lang_name] = Parser(lang_obj)
    return _PARSER_CACHE[lang_name]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_language_supported(file_type: str) -> bool:
    """Check if tree-sitter supports this file type."""
    return file_type in LANGUAGE_MAP


def extract_file(
    source_code: str,
    source_path: str,
    file_type: str,
    chunks: list[Chunk],
) -> list[HyperedgeRecord]:
    """Extract edges from a full source file using tree-sitter.

    Associates edges back to chunks by line range.
    Raises ValueError if the language is not supported.
    """
    lang_name = LANGUAGE_MAP.get(file_type)
    if lang_name is None:
        raise ValueError(f"Unsupported file type: {file_type}")

    parser = _get_parser(lang_name)
    tree = parser.parse(source_code.encode("utf-8", errors="replace"))
    root = tree.root_node

    module_name = _module_name_from_path(source_path)
    ctx = _ExtractionContext(
        lang_name=lang_name,
        module_name=module_name,
        source_path=source_path,
        source_code=source_code,
    )

    _walk_and_extract(root, ctx)

    # Module-level DEFINES edge listing all top-level symbols
    if ctx.top_level_symbols:
        eid = _make_edge_id("DEFINES", module_name, source_path, 0)
        ctx.edges.insert(0, HyperedgeRecord(
            edge_id=eid,
            relation=f"module {module_name} defines: {', '.join(s.split('.')[-1] for s in ctx.top_level_symbols)}",
            edge_type=EdgeType.DEFINES,
            sources=[module_name],
            targets=list(ctx.top_level_symbols),
            source_path=source_path,
            chunk_id="",
            chunk_text="",
        ))

    # Associate edges to chunks by line range
    _associate_chunks(ctx.edges, chunks)

    return ctx.edges


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _module_name_from_path(source_path: str) -> str:
    """Extract a module name from a file path."""
    from pathlib import Path
    return Path(source_path).stem


_edge_counters: dict[str, int] = {}


def _make_edge_id(edge_type: str, key: str, source_path: str, line: int) -> str:
    """Generate a unique edge ID."""
    counter_key = f"{source_path}"
    _edge_counters.setdefault(counter_key, 0)
    _edge_counters[counter_key] += 1
    raw = f"{edge_type}_{key}_{source_path}_{_edge_counters[counter_key]}"
    return md5(raw.encode()).hexdigest()[:16]


class _ExtractionContext:
    """Mutable context accumulated during tree walk."""

    def __init__(self, lang_name: str, module_name: str, source_path: str, source_code: str):
        self.lang_name = lang_name
        self.module_name = module_name
        self.source_path = source_path
        self.source_code = source_code
        self.edges: list[HyperedgeRecord] = []
        self.top_level_symbols: list[str] = []
        self._edge_counter = 0

    def make_edge(
        self,
        sources: list[str],
        targets: list[str],
        relation: str,
        edge_type: str,
        line: int = 0,
        **meta: object,
    ) -> HyperedgeRecord:
        """Create and register a HyperedgeRecord."""
        sources = [s for s in sources if s]
        targets = [t for t in targets if t]
        if not sources and not targets:
            return None  # type: ignore[return-value]
        self._edge_counter += 1
        raw = f"{edge_type}_{relation[:30]}_{self.source_path}_{self._edge_counter}"
        eid = md5(raw.encode()).hexdigest()[:16]
        meta["line"] = line
        edge = HyperedgeRecord(
            edge_id=eid,
            relation=relation,
            edge_type=edge_type,
            sources=sources,
            targets=targets,
            source_path=self.source_path,
            chunk_id="",
            chunk_text="",
            metadata=dict(meta),
        )
        self.edges.append(edge)
        return edge


def _node_text(node: Node) -> str:
    """Get the text content of a node as a string."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _find_enclosing_function(node: Node, lang: str) -> str | None:
    """Walk up the tree to find the enclosing function/method name, qualified with class."""
    func_types = _FUNCTION_DEF_TYPES.get(lang, set())
    class_types = _CLASS_DEF_TYPES.get(lang, set())

    current = node.parent
    func_name = None
    while current is not None:
        if current.type in func_types and func_name is None:
            func_name = _get_def_name(current, lang)
        if current.type in class_types and func_name is not None:
            class_name = _get_def_name(current, lang)
            if class_name:
                return f"{class_name}.{func_name}"
        current = current.parent

    return func_name


def _find_enclosing_class(node: Node, lang: str) -> str | None:
    """Walk up the tree to find the enclosing class/struct/impl name."""
    class_types = _CLASS_DEF_TYPES.get(lang, set())
    current = node.parent
    while current is not None:
        if current.type in class_types:
            return _get_def_name(current, lang)
        current = current.parent
    return None


def _get_def_name(node: Node, lang: str) -> str | None:
    """Extract the name identifier from a definition node."""
    # Most languages use a child named 'name'
    name_node = node.child_by_field_name("name")
    if name_node:
        return _node_text(name_node)

    # Rust impl blocks: get the type being implemented
    if lang == "rust" and node.type == "impl_item":
        type_node = node.child_by_field_name("type")
        if type_node:
            return _node_text(type_node)
        # Fall back: find first type_identifier child
        for child in node.children:
            if child.type == "type_identifier":
                return _node_text(child)

    # Go method receivers: func (r *Receiver) Name()
    if lang == "go" and node.type == "method_declaration":
        name_node = node.child_by_field_name("name")
        if name_node:
            return _node_text(name_node)

    # Fallback: find first identifier child
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child)

    return None


def _qualify_name(name: str, enclosing_class: str | None, module_name: str) -> str:
    """Qualify a name with its enclosing class (module-qualified) or module."""
    if enclosing_class:
        # enclosing_class is just the short name; qualify with module
        return f"{module_name}.{enclosing_class}.{name}"
    return f"{module_name}.{name}"


# ---------------------------------------------------------------------------
# Per-language node type sets
# ---------------------------------------------------------------------------

_FUNCTION_DEF_TYPES: dict[str, set[str]] = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition", "arrow_function"},
    "typescript": {"function_declaration", "method_definition", "arrow_function"},
    "tsx": {"function_declaration", "method_definition", "arrow_function"},
    "go": {"function_declaration", "method_declaration"},
    "rust": {"function_item"},
    "java": {"method_declaration", "constructor_declaration"},
    "c": {"function_definition"},
    "cpp": {"function_definition"},
    "ruby": {"method", "singleton_method"},
    "php": {"function_definition", "method_declaration"},
}

_CLASS_DEF_TYPES: dict[str, set[str]] = {
    "python": {"class_definition"},
    "javascript": {"class_declaration", "class"},
    "typescript": {"class_declaration", "class", "interface_declaration"},
    "tsx": {"class_declaration", "class", "interface_declaration"},
    "go": set(),  # Go uses type_declaration with type_spec
    "rust": {"impl_item"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration"},
    "c": {"struct_specifier"},
    "cpp": {"class_specifier", "struct_specifier"},
    "ruby": {"class", "module"},
    "php": {"class_declaration", "interface_declaration"},
}

_CALL_TYPES: dict[str, set[str]] = {
    "python": {"call"},
    "javascript": {"call_expression", "new_expression"},
    "typescript": {"call_expression", "new_expression"},
    "tsx": {"call_expression", "new_expression"},
    "go": {"call_expression"},
    "rust": {"call_expression", "macro_invocation"},
    "java": {"method_invocation", "object_creation_expression"},
    "c": {"call_expression"},
    "cpp": {"call_expression"},
    "ruby": {"call", "method_call"},
    "php": {"function_call_expression", "member_call_expression", "scoped_call_expression"},
}

_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
    "c": {"preproc_include"},
    "cpp": {"preproc_include"},
    "ruby": {"call"},  # require/require_relative are method calls in Ruby
    "php": {"use_declaration", "namespace_use_declaration"},
}

_RAISE_TYPES: dict[str, set[str]] = {
    "python": {"raise_statement"},
    "javascript": {"throw_statement"},
    "typescript": {"throw_statement"},
    "tsx": {"throw_statement"},
    "go": set(),  # Go uses return with error
    "rust": set(),  # Rust uses Result/panic!
    "java": {"throw_statement"},
    "c": set(),
    "cpp": {"throw_expression"},
    "ruby": {"call"},  # raise is a method call in Ruby
    "php": {"throw_expression"},
}

_INHERIT_NODES: dict[str, str] = {
    "python": "argument_list",  # class Foo(Bar, Baz)
    "javascript": "class_heritage",
    "typescript": "class_heritage",
    "tsx": "class_heritage",
    "java": "superclass",
    "cpp": "base_class_clause",
    "ruby": "superclass",
    "php": "base_clause",
}

# Type declaration types for Go (struct/interface definitions)
_GO_TYPE_TYPES: set[str] = {"type_declaration"}

# Rust struct/enum/trait definitions
_RUST_DEF_TYPES: set[str] = {"struct_item", "enum_item", "trait_item"}


# ---------------------------------------------------------------------------
# Main tree walker
# ---------------------------------------------------------------------------

def _walk_and_extract(root: Node, ctx: _ExtractionContext) -> None:
    """Walk the entire AST and extract edges."""
    lang = ctx.lang_name

    # Use iterative DFS to avoid recursion limits
    stack: list[tuple[Node, str | None]] = [(root, None)]  # (node, enclosing_class)

    while stack:
        node, enclosing_class = stack.pop()

        # --- DEFINES: class/struct/interface definitions ---
        if node.type in _CLASS_DEF_TYPES.get(lang, set()):
            _extract_class_defines(node, ctx, enclosing_class)
            class_name = _get_def_name(node, lang)
            # Push children with updated class context
            for child in reversed(node.children):
                stack.append((child, class_name))
            continue

        # --- Go type declarations (struct, interface) ---
        if lang == "go" and node.type == "type_declaration":
            _extract_go_type_declaration(node, ctx)

        # --- Rust struct/enum/trait definitions ---
        if lang == "rust" and node.type in _RUST_DEF_TYPES:
            _extract_rust_type_def(node, ctx)

        # --- DEFINES + CALLS + RAISES: function/method definitions ---
        if node.type in _FUNCTION_DEF_TYPES.get(lang, set()):
            _extract_function(node, ctx, enclosing_class)
            # Track as top-level symbol if no enclosing class
            func_name = _get_def_name(node, lang)
            if func_name and enclosing_class is None and node.parent and node.parent.type in ("module", "program", "translation_unit", "source_file", "compilation_unit"):
                ctx.top_level_symbols.append(f"{ctx.module_name}.{func_name}")
            # Don't push children — _extract_function walks them
            continue

        # --- IMPORTS ---
        if node.type in _IMPORT_TYPES.get(lang, set()):
            _extract_import(node, ctx)

        # --- Track top-level class symbols ---
        if node.type in _CLASS_DEF_TYPES.get(lang, set()):
            class_name = _get_def_name(node, lang)
            if class_name and node.parent and node.parent.type in ("module", "program", "translation_unit", "source_file", "compilation_unit"):
                ctx.top_level_symbols.append(f"{ctx.module_name}.{class_name}")

        # Push children
        for child in reversed(node.children):
            stack.append((child, enclosing_class))


# ---------------------------------------------------------------------------
# Edge extraction per type
# ---------------------------------------------------------------------------

def _extract_class_defines(node: Node, ctx: _ExtractionContext, enclosing_class: str | None) -> None:
    """Extract DEFINES + INHERITS edges from a class/struct/interface definition."""
    lang = ctx.lang_name
    class_name = _get_def_name(node, lang)
    if not class_name:
        return

    class_qname = f"{ctx.module_name}.{class_name}"
    line = node.start_point[0] + 1

    # Track as top-level symbol
    if enclosing_class is None and node.parent and node.parent.type in ("module", "program", "translation_unit", "source_file", "compilation_unit"):
        ctx.top_level_symbols.append(class_qname)

    # Collect methods/members defined in this class
    methods: list[str] = []
    func_types = _FUNCTION_DEF_TYPES.get(lang, set())

    for child in _iter_class_body(node, lang):
        if child.type in func_types:
            method_name = _get_def_name(child, lang)
            if method_name:
                methods.append(f"{class_qname}.{method_name}")

    if methods:
        ctx.make_edge(
            sources=[class_qname],
            targets=methods,
            relation=f"{class_name} defines methods: {', '.join(m.split('.')[-1] for m in methods)}",
            edge_type=EdgeType.DEFINES,
            line=line,
            class_name=class_name,
        )

    # INHERITS
    _extract_inheritance(node, ctx, class_qname, class_name, lang, line)


def _iter_class_body(node: Node, lang: str):
    """Iterate over the body/member nodes of a class definition."""
    # Most languages nest members in a body/block child
    body_field_names = ["body", "class_body", "declaration_list", "members"]
    for fname in body_field_names:
        body = node.child_by_field_name(fname)
        if body:
            yield from body.children
            return

    # Fallback: iterate named children directly
    for child in node.named_children:
        yield child


def _extract_inheritance(
    node: Node, ctx: _ExtractionContext,
    class_qname: str, class_name: str, lang: str, line: int,
) -> None:
    """Extract INHERITS edge from a class definition."""
    bases: list[str] = []

    if lang == "python":
        # class Foo(Bar, Baz): — bases are in argument_list
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.named_children:
                    name = _extract_name_from_node(arg)
                    if name:
                        bases.append(name)

    elif lang in ("javascript", "typescript", "tsx"):
        # JS: class_heritage > identifier (directly)
        # TS: class_heritage > extends_clause > identifier
        heritage = _find_child_by_type(node, "class_heritage")
        if heritage:
            for child in heritage.named_children:
                if child.type in ("extends_clause", "implements_clause"):
                    # TS wraps in extends_clause / implements_clause
                    for sub in child.named_children:
                        name = _extract_name_from_node(sub)
                        if name:
                            bases.append(name)
                elif child.type in ("identifier", "type_identifier", "member_expression"):
                    # JS puts identifier directly under class_heritage
                    bases.append(_node_text(child))

    elif lang == "java":
        # superclass node contains "extends BaseRepo" — find the type_identifier child
        superclass = node.child_by_field_name("superclass")
        if superclass:
            for child in superclass.named_children:
                if child.type in ("type_identifier", "identifier", "scoped_type_identifier"):
                    bases.append(_node_text(child))
        interfaces = node.child_by_field_name("interfaces")
        if interfaces:
            for child in interfaces.named_children:
                if child.type in ("type_identifier", "identifier", "type_list"):
                    if child.type == "type_list":
                        for sub in child.named_children:
                            bases.append(_node_text(sub))
                    else:
                        bases.append(_node_text(child))

    elif lang == "cpp":
        for child in node.children:
            if child.type == "base_class_clause":
                for spec in child.named_children:
                    name = _extract_name_from_node(spec)
                    if name:
                        bases.append(name)

    elif lang == "ruby":
        superclass = node.child_by_field_name("superclass")
        if superclass:
            name = _extract_name_from_node(superclass)
            if name:
                bases.append(name)

    elif lang == "php":
        for child in node.children:
            if child.type == "base_clause":
                for name_node in child.named_children:
                    name = _extract_name_from_node(name_node)
                    if name:
                        bases.append(name)
            elif child.type == "class_interface_clause":
                for name_node in child.named_children:
                    name = _extract_name_from_node(name_node)
                    if name:
                        bases.append(name)

    elif lang == "rust":
        # impl Trait for Type — extract trait name
        if node.type == "impl_item":
            trait_node = node.child_by_field_name("trait")
            if trait_node:
                name = _extract_name_from_node(trait_node)
                if name:
                    bases.append(name)

    if bases:
        ctx.make_edge(
            sources=[class_qname],
            targets=bases,
            relation=f"{class_name} inherits from {', '.join(bases)}",
            edge_type=EdgeType.INHERITS,
            line=line,
            class_name=class_name,
        )


def _extract_function(node: Node, ctx: _ExtractionContext, enclosing_class: str | None) -> None:
    """Extract CALLS, RAISES, and SIGNATURE edges from a function/method."""
    lang = ctx.lang_name
    func_name = _get_def_name(node, lang)
    if not func_name:
        return

    # Qualify with class or module
    func_qname = _qualify_name(func_name, enclosing_class, ctx.module_name)

    # For Go methods, qualify with receiver type
    if lang == "go" and node.type == "method_declaration":
        receiver = _get_go_receiver_type(node)
        if receiver:
            func_qname = f"{receiver}.{func_name}"

    line = node.start_point[0] + 1

    # --- CALLS ---
    call_types = _CALL_TYPES.get(lang, set())
    call_names: list[str] = []
    for call_node in _find_descendants(node, call_types):
        name = _extract_call_target(call_node, lang)
        if name and name != func_name and name != func_qname:
            call_names.append(name)
    call_names = list(dict.fromkeys(call_names))  # deduplicate preserving order
    if call_names:
        ctx.make_edge(
            sources=[func_qname],
            targets=call_names,
            relation=f"{func_name} calls: {', '.join(call_names)}",
            edge_type=EdgeType.CALLS,
            line=line,
        )

    # --- RAISES ---
    raise_types = _RAISE_TYPES.get(lang, set())
    if raise_types:
        raises: list[str] = []
        for raise_node in _find_descendants(node, raise_types):
            exc_name = _extract_raise_target(raise_node, lang)
            if exc_name:
                raises.append(exc_name)
        raises = list(dict.fromkeys(raises))
        if raises:
            ctx.make_edge(
                sources=[func_qname],
                targets=raises,
                relation=f"{func_name} raises: {', '.join(raises)}",
                edge_type=EdgeType.RAISES,
                line=line,
            )

    # --- SIGNATURE (Python only for now — matches original extractor) ---
    if lang == "python":
        _extract_python_signature(node, ctx, func_qname, func_name, line)


def _extract_python_signature(
    node: Node, ctx: _ExtractionContext,
    func_qname: str, func_name: str, line: int,
) -> None:
    """Extract SIGNATURE edge from Python function parameters and return type."""
    annotations: list[str] = []

    params = node.child_by_field_name("parameters")
    if params:
        for param in params.named_children:
            # typed_parameter or typed_default_parameter
            type_node = param.child_by_field_name("type")
            if type_node:
                ann = _extract_name_from_node(type_node)
                if ann:
                    annotations.append(ann)

    # Return type annotation
    return_type = node.child_by_field_name("return_type")
    if return_type:
        ann = _extract_name_from_node(return_type)
        if ann:
            annotations.append(ann)

    if annotations:
        ctx.make_edge(
            sources=[func_qname],
            targets=annotations,
            relation=f"{func_name} signature involves types: {', '.join(annotations)}",
            edge_type=EdgeType.SIGNATURE,
            line=line,
        )


def _extract_import(node: Node, ctx: _ExtractionContext) -> None:
    """Extract IMPORTS edge from an import/use/include statement."""
    lang = ctx.lang_name
    line = node.start_point[0] + 1

    if lang == "python":
        _extract_python_import(node, ctx, line)
    elif lang in ("javascript", "typescript", "tsx"):
        _extract_js_import(node, ctx, line)
    elif lang == "go":
        _extract_go_import(node, ctx, line)
    elif lang == "rust":
        _extract_rust_import(node, ctx, line)
    elif lang == "java":
        _extract_java_import(node, ctx, line)
    elif lang in ("c", "cpp"):
        _extract_c_include(node, ctx, line)
    elif lang == "ruby":
        _extract_ruby_import(node, ctx, line)
    elif lang == "php":
        _extract_php_import(node, ctx, line)


def _extract_python_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from Python import/import_from statements."""
    if node.type == "import_statement":
        imported: list[str] = []
        for child in node.named_children:
            if child.type == "dotted_name":
                imported.append(_node_text(child))
            elif child.type == "aliased_import":
                alias = child.child_by_field_name("alias")
                name = child.child_by_field_name("name")
                imported.append(_node_text(alias) if alias else _node_text(name) if name else "")
        imported = [i for i in imported if i]
        if imported:
            ctx.make_edge(
                sources=[ctx.module_name],
                targets=imported,
                relation=f"{ctx.module_name} imports {', '.join(imported)}",
                edge_type=EdgeType.IMPORTS,
                line=line,
            )

    elif node.type == "import_from_statement":
        # from X import Y, Z
        module_node = node.child_by_field_name("module_name")
        module = _node_text(module_node) if module_node else ""

        imported: list[str] = []
        for child in node.named_children:
            if child.type == "dotted_name" and child != module_node:
                imported.append(_node_text(child))
            elif child.type == "aliased_import":
                alias = child.child_by_field_name("alias")
                name = child.child_by_field_name("name")
                imported.append(_node_text(alias) if alias else _node_text(name) if name else "")

        imported = [i for i in imported if i]
        if imported:
            targets = [module] + imported if module else imported
            ctx.make_edge(
                sources=[ctx.module_name],
                targets=targets,
                relation=f"{ctx.module_name} imports {', '.join(imported)} from {module}",
                edge_type=EdgeType.IMPORTS,
                line=line,
                from_module=module,
            )


def _extract_js_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from JS/TS import statements."""
    imported: list[str] = []
    module = ""

    for child in node.children:
        if child.type == "string":
            module = _node_text(child).strip("'\"")
        elif child.type == "import_clause":
            for sub in child.named_children:
                if sub.type == "identifier":
                    imported.append(_node_text(sub))
                elif sub.type == "named_imports":
                    for spec in sub.named_children:
                        if spec.type == "import_specifier":
                            name_node = spec.child_by_field_name("name")
                            alias = spec.child_by_field_name("alias")
                            imported.append(_node_text(alias) if alias else _node_text(name_node) if name_node else "")
                elif sub.type == "namespace_import":
                    # import * as X from '...'
                    for id_node in sub.named_children:
                        if id_node.type == "identifier":
                            imported.append(_node_text(id_node))

    imported = [i for i in imported if i]
    targets = [module] + imported if module else imported
    if targets:
        ctx.make_edge(
            sources=[ctx.module_name],
            targets=targets,
            relation=f"{ctx.module_name} imports {', '.join(imported or [module])}",
            edge_type=EdgeType.IMPORTS,
            line=line,
        )


def _extract_go_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from Go import declarations."""
    imported: list[str] = []
    for child in _find_descendants(node, {"import_spec"}):
        path_node = child.child_by_field_name("path")
        if path_node:
            imported.append(_node_text(path_node).strip('"'))
    # Also handle single import
    for child in node.named_children:
        if child.type == "interpreted_string_literal":
            imported.append(_node_text(child).strip('"'))

    imported = list(dict.fromkeys(imported))
    if imported:
        ctx.make_edge(
            sources=[ctx.module_name],
            targets=imported,
            relation=f"{ctx.module_name} imports {', '.join(imported)}",
            edge_type=EdgeType.IMPORTS,
            line=line,
        )


def _extract_rust_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from Rust use declarations."""
    # use std::collections::HashMap;
    text = _node_text(node)
    # Extract the path after 'use '
    path = text.replace("use ", "").replace(";", "").strip()
    if path:
        # Handle braces: use std::{io, fs}
        if "{" in path:
            base = path[:path.index("{")].rstrip("::")
            items = path[path.index("{") + 1:path.index("}")].split(",")
            imported = [f"{base}::{item.strip()}" for item in items if item.strip()]
        else:
            imported = [path]
        ctx.make_edge(
            sources=[ctx.module_name],
            targets=imported,
            relation=f"{ctx.module_name} imports {', '.join(imported)}",
            edge_type=EdgeType.IMPORTS,
            line=line,
        )


def _extract_java_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from Java import declarations."""
    text = _node_text(node).replace("import ", "").replace(";", "").strip()
    if text.startswith("static "):
        text = text[7:]
    if text:
        ctx.make_edge(
            sources=[ctx.module_name],
            targets=[text],
            relation=f"{ctx.module_name} imports {text}",
            edge_type=EdgeType.IMPORTS,
            line=line,
        )


def _extract_c_include(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from C/C++ #include directives."""
    path_node = node.child_by_field_name("path")
    if path_node:
        path = _node_text(path_node).strip('"<>')
    else:
        # Fall back to extracting from text
        text = _node_text(node)
        import re
        m = re.search(r'[<"]([^">]+)[>"]', text)
        path = m.group(1) if m else ""

    if path:
        ctx.make_edge(
            sources=[ctx.module_name],
            targets=[path],
            relation=f"{ctx.module_name} includes {path}",
            edge_type=EdgeType.IMPORTS,
            line=line,
        )


def _extract_ruby_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from Ruby require/require_relative calls."""
    # In Ruby, require is a method call
    if node.type != "call":
        return
    method_node = node.child_by_field_name("method")
    if not method_node:
        return
    method_name = _node_text(method_node)
    if method_name not in ("require", "require_relative"):
        return

    args = node.child_by_field_name("arguments")
    if args:
        for arg in args.named_children:
            path = _node_text(arg).strip("'\"")
            if path:
                ctx.make_edge(
                    sources=[ctx.module_name],
                    targets=[path],
                    relation=f"{ctx.module_name} requires {path}",
                    edge_type=EdgeType.IMPORTS,
                    line=line,
                )


def _extract_php_import(node: Node, ctx: _ExtractionContext, line: int) -> None:
    """Extract IMPORTS from PHP use declarations."""
    text = _node_text(node)
    # use Namespace\ClassName;
    path = text.replace("use ", "").replace(";", "").strip()
    if path:
        ctx.make_edge(
            sources=[ctx.module_name],
            targets=[path],
            relation=f"{ctx.module_name} imports {path}",
            edge_type=EdgeType.IMPORTS,
            line=line,
        )


def _extract_go_type_declaration(node: Node, ctx: _ExtractionContext) -> None:
    """Extract DEFINES edges from Go type declarations (struct, interface)."""
    for child in node.named_children:
        if child.type == "type_spec":
            type_name_node = child.child_by_field_name("name")
            if not type_name_node:
                continue
            type_name = _node_text(type_name_node)
            type_qname = f"{ctx.module_name}.{type_name}"
            line = child.start_point[0] + 1

            # Track as top-level symbol
            ctx.top_level_symbols.append(type_qname)

            type_body = child.child_by_field_name("type")
            if type_body and type_body.type in ("struct_type", "interface_type"):
                # Collect field/method names
                members: list[str] = []
                body = type_body.child_by_field_name("body") or type_body
                if body:
                    for field in body.named_children:
                        if field.type == "field_declaration":
                            name_node = field.child_by_field_name("name")
                            if name_node:
                                members.append(f"{type_qname}.{_node_text(name_node)}")
                        elif field.type == "method_spec":
                            name_node = field.child_by_field_name("name")
                            if name_node:
                                members.append(f"{type_qname}.{_node_text(name_node)}")

                if members:
                    ctx.make_edge(
                        sources=[type_qname],
                        targets=members,
                        relation=f"{type_name} defines: {', '.join(m.split('.')[-1] for m in members)}",
                        edge_type=EdgeType.DEFINES,
                        line=line,
                    )


def _extract_rust_type_def(node: Node, ctx: _ExtractionContext) -> None:
    """Extract DEFINES from Rust struct/enum/trait definitions."""
    name = _get_def_name(node, "rust")
    if not name:
        return

    qname = f"{ctx.module_name}.{name}"
    line = node.start_point[0] + 1
    ctx.top_level_symbols.append(qname)

    # For struct: collect fields
    # For enum: collect variants
    members: list[str] = []

    if node.type == "struct_item":
        body = node.child_by_field_name("body")
        if body:
            for field in body.named_children:
                if field.type == "field_declaration":
                    fname_node = field.child_by_field_name("name")
                    if fname_node:
                        members.append(f"{qname}.{_node_text(fname_node)}")

    elif node.type == "enum_item":
        body = node.child_by_field_name("body")
        if body:
            for variant in body.named_children:
                if variant.type == "enum_variant":
                    vname = _get_def_name(variant, "rust")
                    if vname:
                        members.append(f"{qname}.{vname}")

    if members:
        ctx.make_edge(
            sources=[qname],
            targets=members,
            relation=f"{name} defines: {', '.join(m.split('.')[-1] for m in members)}",
            edge_type=EdgeType.DEFINES,
            line=line,
        )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_call_target(node: Node, lang: str) -> str | None:
    """Extract the name of the function/method being called."""
    if lang == "python":
        func = node.child_by_field_name("function")
        if func:
            return _extract_name_from_node(func)
        return None

    elif lang in ("javascript", "typescript", "tsx"):
        func = node.child_by_field_name("function")
        if func:
            return _extract_name_from_node(func)
        return None

    elif lang in ("go", "c", "cpp"):
        func = node.child_by_field_name("function")
        if func:
            return _extract_name_from_node(func)
        return None

    elif lang == "rust":
        if node.type == "macro_invocation":
            macro = node.child_by_field_name("macro")
            if macro:
                return _node_text(macro) + "!"
            return None
        func = node.child_by_field_name("function")
        if func:
            return _extract_name_from_node(func)
        return None

    elif lang == "java":
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            if obj and name_node:
                return f"{_extract_name_from_node(obj)}.{_node_text(name_node)}"
            elif name_node:
                return _node_text(name_node)
        elif node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node:
                return _extract_name_from_node(type_node)
        return None

    elif lang == "ruby":
        method = node.child_by_field_name("method")
        receiver = node.child_by_field_name("receiver")
        if receiver and method:
            return f"{_extract_name_from_node(receiver)}.{_node_text(method)}"
        elif method:
            return _node_text(method)
        return None

    elif lang == "php":
        if node.type == "function_call_expression":
            func = node.child_by_field_name("function")
            if func:
                return _extract_name_from_node(func)
        elif node.type == "member_call_expression":
            name = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            if obj and name:
                return f"{_extract_name_from_node(obj)}.{_node_text(name)}"
        elif node.type == "scoped_call_expression":
            name = node.child_by_field_name("name")
            scope = node.child_by_field_name("scope")
            if scope and name:
                return f"{_extract_name_from_node(scope)}::{_node_text(name)}"
        return None

    return None


def _extract_raise_target(node: Node, lang: str) -> str | None:
    """Extract the exception/error type from a raise/throw statement."""
    if lang == "python":
        # raise ValueError("msg") → look for the call expression
        for child in node.named_children:
            name = _extract_name_from_node(child)
            if name:
                # Strip call args: ValueError("msg") → ValueError
                return name.split("(")[0] if "(" in name else name
        return None

    elif lang in ("javascript", "typescript", "tsx"):
        # throw new Error("msg")
        for child in node.named_children:
            if child.type == "new_expression":
                constructor = child.child_by_field_name("constructor")
                if constructor:
                    return _extract_name_from_node(constructor)
            name = _extract_name_from_node(child)
            if name:
                return name
        return None

    elif lang == "java":
        # throw new Exception("msg")
        for child in node.named_children:
            if child.type == "object_creation_expression":
                type_node = child.child_by_field_name("type")
                if type_node:
                    return _extract_name_from_node(type_node)
        return None

    elif lang in ("cpp", "php"):
        for child in node.named_children:
            name = _extract_name_from_node(child)
            if name:
                return name
        return None

    elif lang == "ruby":
        # raise is a method call: raise StandardError.new("msg")
        if node.type == "call":
            method = node.child_by_field_name("method")
            if method and _node_text(method) == "raise":
                args = node.child_by_field_name("arguments")
                if args and args.named_children:
                    return _extract_name_from_node(args.named_children[0])
        return None

    return None


def _extract_name_from_node(node: Node) -> str | None:
    """Extract a dotted name from a node (handles identifiers, member access, etc.)."""
    if node is None:
        return None

    if node.type in ("identifier", "type_identifier", "constant", "scope_resolution"):
        return _node_text(node)

    if node.type in ("attribute", "member_expression", "field_expression", "scoped_identifier"):
        return _node_text(node)

    if node.type in ("dotted_name",):
        return _node_text(node)

    if node.type in ("qualified_identifier", "name"):
        return _node_text(node)

    if node.type == "call":
        func = node.child_by_field_name("function")
        if func:
            return _extract_name_from_node(func)

    if node.type in ("subscript", "generic_type"):
        # e.g., List[int] → List
        first = node.named_children[0] if node.named_children else None
        if first:
            return _extract_name_from_node(first)

    if node.type in ("string", "interpreted_string_literal"):
        return _node_text(node).strip("'\"")

    # Last resort: return text of named node
    if node.is_named and node.child_count == 0:
        return _node_text(node)

    # For compound types, try to get text
    text = _node_text(node).strip()
    if text and len(text) < 100 and "\n" not in text:
        return text

    return None


def _find_descendants(node: Node, types: set[str]) -> list[Node]:
    """Find all descendant nodes matching any of the given types."""
    results: list[Node] = []
    stack = list(node.children)
    while stack:
        child = stack.pop()
        if child.type in types:
            results.append(child)
        stack.extend(child.children)
    return results


def _find_child_by_type(node: Node, type_name: str) -> Node | None:
    """Find the first direct child of the given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _get_go_receiver_type(node: Node) -> str | None:
    """Get the receiver type from a Go method declaration."""
    receiver = node.child_by_field_name("receiver")
    if receiver:
        # parameter_list → parameter_declaration → type
        for param in receiver.named_children:
            type_node = param.child_by_field_name("type")
            if type_node:
                text = _node_text(type_node).lstrip("*")
                return text
    return None


# ---------------------------------------------------------------------------
# Chunk association
# ---------------------------------------------------------------------------

def _associate_chunks(edges: list[HyperedgeRecord], chunks: list[Chunk]) -> None:
    """Associate each edge with the best-matching chunk by line number."""
    if not chunks:
        return

    chunk_ranges: list[tuple[int, int, Chunk]] = []
    for chunk in chunks:
        start = chunk.start_line or 1
        end = chunk.end_line or 999999
        chunk_ranges.append((start, end, chunk))

    default_chunk = chunks[0]

    for edge in edges:
        line = edge.metadata.get("line", 0)
        if line > 0:
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
