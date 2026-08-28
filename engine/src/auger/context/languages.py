"""Which parser reads which file, and which nodes are worth naming.

A symbol query per language would be nineteen queries to write and keep. The node type
names are already close enough across grammars that one list covers them all, and a
language that names something differently degrades to a whole file chunk rather than
failing.
"""

from __future__ import annotations

from pathlib import Path

#: File suffix to the name that `tree_sitter_language_pack` knows.
BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".tf": "hcl",
    ".hcl": "hcl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}

#: Node types that name something a reviewer would look for.
SYMBOL_TYPES: frozenset[str] = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_item",
        "method_definition",
        "method_declaration",
        "constructor_declaration",
        "class_definition",
        "class_declaration",
        "class_specifier",
        "interface_declaration",
        "struct_item",
        "struct_specifier",
        "enum_declaration",
        "enum_item",
        "impl_item",
        "trait_item",
        "type_spec",
        "type_alias_declaration",
        "module",
        "object_definition",
        "singleton_method",
    }
)

#: A node that only wraps a symbol. It carries no name of its own, so the map looks
#: inside it and never emits it.
WRAPPER_TYPES: frozenset[str] = frozenset(
    {
        "decorated_definition",
        "export_statement",
        "declaration",
        "labeled_statement",
        # Go wraps `type Server struct{}` in a declaration and names the spec inside it.
        "type_declaration",
    }
)

#: A file the rig never indexes: it holds no reviewable code.
SKIP_SUFFIXES: frozenset[str] = frozenset(
    {".lock", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".zip", ".gz", ".woff2"}
)

#: A dependency lock file. It is generated, it is enormous, and nobody reviews it.
SKIP_NAMES: frozenset[str] = frozenset(
    {
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "bun.lockb",
        "cargo.lock",
        "uv.lock",
        "poetry.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
    }
)

#: A file larger than this is data, not code.
MAX_FILE_BYTES = 512_000


def language_for(path: str | Path) -> str | None:
    return BY_SUFFIX.get(Path(path).suffix.lower())


def indexable(path: str | Path, size: int) -> bool:
    name = Path(path)
    if size > MAX_FILE_BYTES or size == 0:
        return False
    if name.suffix.lower() in SKIP_SUFFIXES or name.name.lower() in SKIP_NAMES:
        return False
    return not (name.name.endswith(".min.js") or name.name.endswith(".min.css"))
