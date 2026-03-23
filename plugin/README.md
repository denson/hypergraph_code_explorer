# HCE Plugin

Hypergraph Code Explorer plugin for Claude. Indexes any codebase into a hypergraph and provides structured queries for understanding code architecture.

## Components

- **MCP Server** (`hce`) — Exposes 6 tools: `hce_index`, `hce_lookup`, `hce_search`, `hce_query`, `hce_overview`, `hce_stats`
- **Skill** (`hce-explore`) — Guides Claude through the index-and-query workflow

## Setup

No configuration needed. The plugin auto-installs HCE from GitHub on first use.

Supports Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby, and PHP (including mixed-language projects).

## Usage

Point Claude at any codebase and ask about its structure:

- "Index this repo with HCE and show me the architecture"
- "What are the most important symbols in this codebase?"
- "How does authentication work in this code?"
- "What calls the request handler?"

The plugin will automatically index the codebase (if not already cached) and use the hypergraph to answer structural questions without reading every source file.

## Requirements

- Python 3.10+
- Git (for initial install from GitHub)
