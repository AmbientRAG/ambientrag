"""AmbientRAG architecture diagram generator — Mermaid diagrams from installed CAPs."""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from ambientrag.state import get_installed_caps, get_vault_path, is_cap_active, is_initialized
from ambientrag.utils import print_info, print_success

console = Console()


def generate_pipeline_diagram(installed: dict) -> str:
    """Generate a Mermaid pipeline diagram based on installed CAPs.

    The pipeline flows left-to-right. Each CAP inserts its stage at the
    correct position. Installed stages are green, available-but-not-installed
    stages are dashed grey.
    """
    active = {k for k, v in installed.items() if v.get("enabled", True)}

    lines = ["```mermaid", "graph LR"]

    # --- Nodes ---
    # Always present (CAP-001 foundation)
    lines.append('    NOTES["Markdown Notes"]')
    lines.append('    CHUNK["Chunker"]')

    if "002" in active:
        lines.append('    ENRICH["HyDE Enrichment<br/><i>CAP-002</i>"]')
    elif "002" in installed:
        lines.append('    ENRICH["HyDE Enrichment<br/><i>CAP-002 disabled</i>"]:::disabled')
    else:
        lines.append('    ENRICH["HyDE Enrichment<br/><i>CAP-002</i>"]:::available')

    lines.append('    EMBED["Embedder"]')
    lines.append('    DB[("pgvector")]')
    lines.append('    SEARCH["Hybrid Search<br/><i>semantic + keyword</i>"]')

    if "004" in active:
        lines.append('    TEMPORAL["Temporal Reranking<br/><i>CAP-004</i>"]')
    elif "004" in installed:
        lines.append('    TEMPORAL["Temporal Reranking<br/><i>CAP-004 disabled</i>"]:::disabled')
    else:
        lines.append('    TEMPORAL["Temporal Reranking<br/><i>CAP-004</i>"]:::available')

    if "005" in active:
        lines.append('    RERANK["Cross-Encoder Reranker<br/><i>CAP-005</i>"]')
    elif "005" in installed:
        lines.append('    RERANK["Cross-Encoder Reranker<br/><i>CAP-005 disabled</i>"]:::disabled')
    else:
        lines.append('    RERANK["Cross-Encoder Reranker<br/><i>CAP-005</i>"]:::available')

    lines.append('    MCP["MCP Server<br/><i>:8100</i>"]')
    lines.append('    AI["AI Assistant"]')

    # --- Token hygiene (TOOL-002, not a CAP) ---
    lines.append('    TOKENS["Token Tracking<br/><i>TOOL-002</i>"]:::available')

    # --- Caveman cache (side node for CAP-003) ---
    if "003" in active:
        lines.append('    CAVEMAN["Caveman Cache<br/><i>CAP-003</i>"]')
    elif "003" in installed:
        lines.append('    CAVEMAN["Caveman Cache<br/><i>CAP-003 disabled</i>"]:::disabled')
    else:
        lines.append('    CAVEMAN["Caveman Cache<br/><i>CAP-003</i>"]:::available')

    lines.append("")

    # --- Edges (main pipeline) ---
    lines.append("    NOTES --> CHUNK")
    lines.append("    CHUNK --> ENRICH")
    lines.append("    ENRICH --> EMBED")
    lines.append("    EMBED --> DB")
    lines.append("    DB --> SEARCH")
    lines.append("    SEARCH --> TEMPORAL")
    lines.append("    TEMPORAL --> RERANK")
    lines.append("    RERANK --> MCP")
    lines.append("    MCP --> AI")

    # --- Side edges ---
    lines.append("    MCP -.- TOKENS")
    lines.append("    SEARCH -.- CAVEMAN")

    lines.append("")

    # --- Styles ---
    # Installed + active = green
    installed_nodes = []
    available_nodes = []

    # CAP-001 nodes are always installed if we got here
    installed_nodes.extend(["NOTES", "CHUNK", "EMBED", "DB", "SEARCH", "MCP", "AI"])

    for cap, nodes in [
        ("002", ["ENRICH"]),
        ("003", ["CAVEMAN"]),
        ("004", ["TEMPORAL"]),
        ("005", ["RERANK"]),
    ]:
        if cap in active:
            installed_nodes.extend(nodes)
        else:
            available_nodes.extend(nodes)

    # TOKENS is a tool (TOOL-002), not a CAP — always shown as available
    available_nodes.append("TOKENS")

    lines.append("    classDef installed fill:#2a9d8f,stroke:#264653,color:#fff")
    lines.append("    classDef available fill:#e9ecef,stroke:#adb5bd,color:#6c757d,stroke-dasharray: 5 5")
    lines.append("    classDef disabled fill:#f4a261,stroke:#e76f51,color:#fff,stroke-dasharray: 5 5")

    if installed_nodes:
        lines.append(f"    class {','.join(installed_nodes)} installed")
    if available_nodes:
        lines.append(f"    class {','.join(available_nodes)} available")

    lines.append("```")

    return "\n".join(lines)


def generate_dependency_diagram(installed: dict) -> str:
    """Generate a Mermaid diagram of the CAP dependency graph."""
    active = {k for k, v in installed.items() if v.get("enabled", True)}

    lines = ["```mermaid", "graph TD"]

    # Nodes
    caps = {
        "001": "CAP-001<br/>Vector Search",
        "002": "CAP-002<br/>Enrichment",
        "003": "CAP-003<br/>Tiered Retrieval",
        "004": "CAP-004<br/>Temporal Scoring",
        "005": "CAP-005<br/>Reranker",
    }

    for cap_id, label in caps.items():
        if cap_id in active:
            lines.append(f'    C{cap_id}["{label}"]:::installed')
        elif cap_id in installed:
            lines.append(f'    C{cap_id}["{label}"]:::disabled')
        else:
            lines.append(f'    C{cap_id}["{label}"]:::available')

    lines.append("")

    # Edges (from manifest)
    lines.append("    C001 --> C002")
    lines.append("    C002 --> C003")
    lines.append("    C001 --> C004")
    lines.append("    C001 --> C005")

    lines.append("")

    lines.append("    classDef installed fill:#2a9d8f,stroke:#264653,color:#fff")
    lines.append("    classDef available fill:#e9ecef,stroke:#adb5bd,color:#6c757d,stroke-dasharray: 5 5")
    lines.append("    classDef disabled fill:#f4a261,stroke:#e76f51,color:#fff,stroke-dasharray: 5 5")

    lines.append("```")

    return "\n".join(lines)


def generate_full_diagram(installed: dict) -> str:
    """Generate the complete architecture diagram document."""
    active_names = []
    from ambientrag.caps import registry
    all_caps = registry.get_all_caps()
    for cap_id, info in all_caps.items():
        if cap_id in installed and installed[cap_id].get("enabled", True):
            active_names.append(f"CAP-{cap_id} ({info['name']})")

    lines = [
        "<!-- Generated by ambientrag diagram. Renders in Obsidian and GitHub. -->",
        "# AmbientRAG Architecture",
        "",
        f"**Installed:** {', '.join(active_names) if active_names else 'None'}",
        "",
        "## Pipeline",
        "",
        "Green = installed and active. Dashed grey = available but not installed. Orange dashed = disabled.",
        "",
        generate_pipeline_diagram(installed),
        "",
        "## Capability Dependencies",
        "",
        generate_dependency_diagram(installed),
        "",
        "---",
        "*Regenerate with `ambientrag diagram --save`*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

@click.command()
@click.option("--save", is_flag=True, help="Save to vault as _system/architecture.md")
@click.option("--deps", is_flag=True, help="Show only the dependency graph")
@click.option("--pipeline", is_flag=True, help="Show only the pipeline diagram")
def diagram(save: bool, deps: bool, pipeline: bool):
    """Generate architecture diagram from installed CAPs (Mermaid format)."""
    installed = get_installed_caps() if is_initialized() else {}

    if deps and not pipeline:
        output = generate_dependency_diagram(installed)
    elif pipeline and not deps:
        output = generate_pipeline_diagram(installed)
    else:
        output = generate_full_diagram(installed)

    if save:
        vault_path = get_vault_path()
        if not vault_path:
            print_info("No vault configured. Use --vault-path or run `ambientrag init` first.")
            console.print(output)
            return

        target = vault_path / "_system" / "architecture.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8")
        print_success(f"Saved to {target}")
        print_info("Open in Obsidian to see the rendered diagrams.")
    else:
        console.print(output)
