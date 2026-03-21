"""Tests for init command — tool instruction file generation."""

from __future__ import annotations

from hypergraph_code_explorer.init import (
    generate_init_file,
    generate_all_init_files,
    _SECTION_MARKER,
)


def test_generate_claude_code(tmp_path):
    path = generate_init_file("claude-code", tmp_path)
    assert path.name == "CLAUDE.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Code Intelligence" in content
    assert "--json" in content
    assert "hce lookup" in content


def test_generate_cursor(tmp_path):
    path = generate_init_file("cursor", tmp_path)
    assert path.name == ".cursorrules"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Code Intelligence" in content
    assert "hce lookup" in content


def test_generate_codex(tmp_path):
    path = generate_init_file("codex", tmp_path)
    assert path.name == "AGENTS.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Code Intelligence" in content
    assert "hce overview" in content


def test_generate_all(tmp_path):
    paths = generate_all_init_files(tmp_path)
    assert len(paths) == 3
    names = {p.name for p in paths}
    assert names == {"CLAUDE.md", ".cursorrules", "AGENTS.md"}
    for p in paths:
        assert p.exists()


def test_idempotent_no_duplication(tmp_path):
    """Calling generate twice should replace, not duplicate the section."""
    generate_init_file("claude-code", tmp_path)
    generate_init_file("claude-code", tmp_path)
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    count = content.count("## Code Intelligence")
    assert count == 1, f"Section duplicated: found {count} times"


def test_preserves_existing_content(tmp_path):
    """Pre-existing content before the section should be preserved."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My Project\n\nSome existing instructions.\n", encoding="utf-8")

    generate_init_file("claude-code", tmp_path)
    content = claude_md.read_text(encoding="utf-8")

    assert "# My Project" in content
    assert "Some existing instructions." in content
    assert "## Code Intelligence" in content
    assert "hce lookup" in content


def test_replaces_existing_section(tmp_path):
    """If Code Intelligence section already exists, replace it."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# My Project\n\n## Code Intelligence\n\nOld instructions here.\n\n## Other Section\n\nKeep this.\n",
        encoding="utf-8",
    )

    generate_init_file("claude-code", tmp_path)
    content = claude_md.read_text(encoding="utf-8")

    assert "# My Project" in content
    assert "Old instructions here." not in content
    assert "hce lookup" in content
    assert "## Other Section" in content
    assert "Keep this." in content
    assert content.count("## Code Intelligence") == 1


def test_invalid_tool_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="Unknown tool"):
        generate_init_file("unknown-tool", tmp_path)
