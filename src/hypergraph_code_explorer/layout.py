"""
Force-Directed Layout Engine
=============================
Precomputed force-directed layout using numpy for deterministic, fast graph
positioning. Replaces the browser-side D3 force simulation.

Force model (matches D3 config):
  - DEFINES edges: short rest length, strong spring (cluster parent-child tight)
  - INHERITS edges: medium rest length
  - CALLS/other edges: longer rest length, weaker spring (callers spread out)
  - Grid-based repulsion approximation for O(n + g^2) performance
  - Centering force toward origin

For graphs >5000 nodes, uses a two-phase approach:
  1. Subsample the top-5000 most important nodes for force simulation
  2. Place remaining nodes near their most-connected simulated neighbor
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np


# ---------------------------------------------------------------------------
# Edge-type spring parameters
# ---------------------------------------------------------------------------

_SPRING_PARAMS: dict[str, tuple[float, float]] = {
    # edge_type: (rest_length, spring_constant)
    "DEFINES":   (40.0,  0.05),
    "INHERITS":  (80.0,  0.03),
    "CALLS":     (150.0, 0.015),
    "IMPORTS":   (120.0, 0.01),
    "SIGNATURE": (60.0,  0.02),
    "RAISES":    (100.0, 0.015),
    "DECORATES": (50.0,  0.03),
}
_DEFAULT_SPRING = (120.0, 0.015)

# Simulation parameters
_REPULSION_STRENGTH = 800.0
_CENTER_STRENGTH = 0.002
_VELOCITY_DAMPING = 0.85
_MAX_DISPLACEMENT = 50.0

# Grid resolution for grid-based repulsion (higher = more accurate but slower)
_GRID_SIZE = 32

# Threshold for using grid-based repulsion vs direct O(n^2)
_GRID_REPULSION_THRESHOLD = 300

# Subsample threshold: graphs larger than this use two-phase layout
_SUBSAMPLE_THRESHOLD = 5000
_SUBSAMPLE_SIZE = 5000


# ---------------------------------------------------------------------------
# Repulsion strategies
# ---------------------------------------------------------------------------

def _compute_repulsion_direct(
    positions: np.ndarray,
    strength: float,
    forces: np.ndarray,
) -> None:
    """Direct O(n^2) vectorized repulsion for small graphs."""
    n = positions.shape[0]
    # Full pairwise distance matrix via broadcasting
    dx = positions[:, 0:1] - positions[:, 0:1].T  # (N, N)
    dy = positions[:, 1:2] - positions[:, 1:2].T  # (N, N)
    dist_sq = dx * dx + dy * dy + 1.0
    inv_dist = 1.0 / np.sqrt(dist_sq)
    f = strength * inv_dist * inv_dist
    np.fill_diagonal(f, 0.0)
    forces[:, 0] += np.sum(f * dx * inv_dist, axis=1)
    forces[:, 1] += np.sum(f * dy * inv_dist, axis=1)


def _compute_repulsion_grid(
    positions: np.ndarray,
    strength: float,
    forces: np.ndarray,
) -> None:
    """Grid-based repulsion approximation — O(n + g^2).

    Bins nodes into a coarse grid, computes cell-to-cell repulsion,
    then distributes forces back to individual nodes. Much faster
    than pairwise for large n.
    """
    n = positions.shape[0]
    g = _GRID_SIZE

    # Compute grid bounds
    x_min, y_min = positions.min(axis=0) - 1.0
    x_max, y_max = positions.max(axis=0) + 1.0
    x_range = x_max - x_min
    y_range = y_max - y_min
    if x_range < 1:
        x_range = 1.0
    if y_range < 1:
        y_range = 1.0

    # Assign each node to a grid cell
    gx = np.clip(((positions[:, 0] - x_min) / x_range * g).astype(np.int32), 0, g - 1)
    gy = np.clip(((positions[:, 1] - y_min) / y_range * g).astype(np.int32), 0, g - 1)

    # Accumulate mass and center of mass per cell
    cell_mass = np.zeros((g, g), dtype=np.float64)
    cell_cx = np.zeros((g, g), dtype=np.float64)
    cell_cy = np.zeros((g, g), dtype=np.float64)

    for i in range(n):
        ci, cj = gx[i], gy[i]
        cell_mass[ci, cj] += 1.0
        cell_cx[ci, cj] += positions[i, 0]
        cell_cy[ci, cj] += positions[i, 1]

    # Compute center of mass for occupied cells
    occupied = cell_mass > 0
    cell_cx[occupied] /= cell_mass[occupied]
    cell_cy[occupied] /= cell_mass[occupied]

    # Compute cell-to-cell repulsion forces
    # For each occupied cell, compute force from all other occupied cells
    occ_idx = np.argwhere(occupied)  # (M, 2) — occupied cell indices
    m = occ_idx.shape[0]
    if m < 2:
        return

    # Cell positions and masses for occupied cells
    occ_cx = cell_cx[occ_idx[:, 0], occ_idx[:, 1]]
    occ_cy = cell_cy[occ_idx[:, 0], occ_idx[:, 1]]
    occ_mass = cell_mass[occ_idx[:, 0], occ_idx[:, 1]]

    # Pairwise cell forces — m is typically << n, so O(m^2) is fast
    dx = occ_cx[:, None] - occ_cx[None, :]  # (M, M)
    dy = occ_cy[:, None] - occ_cy[None, :]
    dist_sq = dx * dx + dy * dy + 1.0
    inv_dist = 1.0 / np.sqrt(dist_sq)
    # Force proportional to mass product
    f = strength * occ_mass[:, None] * inv_dist * inv_dist
    np.fill_diagonal(f, 0.0)
    cell_fx = np.sum(f * dx * inv_dist, axis=1)
    cell_fy = np.sum(f * dy * inv_dist, axis=1)

    # Build cell index for lookup
    cell_force_x = np.zeros((g, g), dtype=np.float64)
    cell_force_y = np.zeros((g, g), dtype=np.float64)
    for k in range(m):
        ci, cj = occ_idx[k]
        cell_force_x[ci, cj] = cell_fx[k] / occ_mass[k]  # per-node force
        cell_force_y[ci, cj] = cell_fy[k] / occ_mass[k]

    # Distribute forces back to nodes
    forces[:, 0] += cell_force_x[gx, gy]
    forces[:, 1] += cell_force_y[gx, gy]


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def compute_layout(
    nodes: list[dict],
    edges: list[dict],
    *,
    iterations: int = 300,
    seed: int = 42,
) -> dict[str, dict[str, float]]:
    """Compute force-directed layout positions for graph nodes.

    Args:
        nodes: List of node dicts with at least ``id``, ``importance``, ``group``.
        edges: List of edge dicts with ``source``, ``target``, ``type``.
        iterations: Number of simulation iterations.
        seed: RNG seed for deterministic layout.

    Returns:
        ``{node_id: {"x": float, "y": float}}`` with positions normalized
        to approximately -1000..1000.
    """
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]["id"]: {"x": 0.0, "y": 0.0}}

    if n > _SUBSAMPLE_THRESHOLD:
        return _two_phase_layout(nodes, edges, iterations=iterations, seed=seed)

    return _simulate(nodes, edges, iterations=iterations, seed=seed)


def _simulate(
    nodes: list[dict],
    edges: list[dict],
    *,
    iterations: int = 300,
    seed: int = 42,
) -> dict[str, dict[str, float]]:
    """Run the full force simulation on all nodes."""
    n = len(nodes)
    rng = np.random.RandomState(seed)

    positions = _initialize_positions(nodes, rng)
    velocities = np.zeros((n, 2), dtype=np.float64)

    id_to_idx = {node["id"]: i for i, node in enumerate(nodes)}

    # Parse edges into arrays
    edge_src = []
    edge_tgt = []
    edge_rest_len = []
    edge_k = []
    for e in edges:
        si = id_to_idx.get(e["source"])
        ti = id_to_idx.get(e["target"])
        if si is None or ti is None or si == ti:
            continue
        edge_src.append(si)
        edge_tgt.append(ti)
        rest, k = _SPRING_PARAMS.get(e.get("type", ""), _DEFAULT_SPRING)
        edge_rest_len.append(rest)
        edge_k.append(k)

    edge_src_arr = np.array(edge_src, dtype=np.int32)
    edge_tgt_arr = np.array(edge_tgt, dtype=np.int32)
    edge_rest_arr = np.array(edge_rest_len, dtype=np.float64)
    edge_k_arr = np.array(edge_k, dtype=np.float64)

    use_grid = n > _GRID_REPULSION_THRESHOLD
    repulsion_fn = _compute_repulsion_grid if use_grid else _compute_repulsion_direct

    for it in range(iterations):
        alpha = max(1.0 - it / iterations, 0.01)

        forces = np.zeros((n, 2), dtype=np.float64)

        # --- Repulsion ---
        repulsion_fn(positions, _REPULSION_STRENGTH * alpha, forces)

        # --- Spring attraction along edges ---
        if len(edge_src_arr) > 0:
            src_pos = positions[edge_src_arr]
            tgt_pos = positions[edge_tgt_arr]
            dx = tgt_pos[:, 0] - src_pos[:, 0]
            dy = tgt_pos[:, 1] - src_pos[:, 1]
            dist = np.sqrt(dx * dx + dy * dy) + 1.0
            displacement = dist - edge_rest_arr
            fx = edge_k_arr * alpha * displacement * dx / dist
            fy = edge_k_arr * alpha * displacement * dy / dist

            np.add.at(forces[:, 0], edge_src_arr, fx)
            np.add.at(forces[:, 1], edge_src_arr, fy)
            np.add.at(forces[:, 0], edge_tgt_arr, -fx)
            np.add.at(forces[:, 1], edge_tgt_arr, -fy)

        # --- Centering force ---
        forces -= positions * _CENTER_STRENGTH * alpha

        # --- Update velocities and positions ---
        velocities = velocities * _VELOCITY_DAMPING + forces
        disp = np.sqrt(velocities[:, 0] ** 2 + velocities[:, 1] ** 2)
        mask = disp > _MAX_DISPLACEMENT
        if mask.any():
            scale = _MAX_DISPLACEMENT / disp[mask]
            velocities[mask, 0] *= scale
            velocities[mask, 1] *= scale

        positions += velocities

    positions = _normalize(positions)

    return {
        nodes[i]["id"]: {"x": round(float(positions[i, 0]), 1), "y": round(float(positions[i, 1]), 1)}
        for i in range(n)
    }


def _initialize_positions(
    nodes: list[dict],
    rng: np.random.RandomState,
) -> np.ndarray:
    """Initialize positions with module-aware seeding."""
    n = len(nodes)
    positions = np.zeros((n, 2), dtype=np.float64)

    groups: dict[str, list[int]] = defaultdict(list)
    for i, node in enumerate(nodes):
        groups[node.get("group", "")].append(i)

    group_list = sorted(groups.keys())
    n_groups = max(len(group_list), 1)
    base_radius = 200 + n_groups * 15

    for gi, group in enumerate(group_list):
        angle = 2 * np.pi * gi / n_groups
        cx = base_radius * np.cos(angle)
        cy = base_radius * np.sin(angle)
        spread = max(30, min(80, len(groups[group]) * 0.3))

        for idx in groups[group]:
            positions[idx, 0] = cx + rng.normal(0, spread)
            positions[idx, 1] = cy + rng.normal(0, spread)

    return positions


def _normalize(positions: np.ndarray) -> np.ndarray:
    """Normalize positions to approximately -1000..1000."""
    if positions.shape[0] == 0:
        return positions
    mins = positions.min(axis=0)
    maxs = positions.max(axis=0)
    ranges = maxs - mins
    ranges[ranges == 0] = 1.0

    centered = positions - (mins + maxs) / 2
    scale = 1000.0 / (ranges.max() / 2) if ranges.max() > 0 else 1.0
    return centered * scale


def _two_phase_layout(
    nodes: list[dict],
    edges: list[dict],
    *,
    iterations: int = 300,
    seed: int = 42,
) -> dict[str, dict[str, float]]:
    """Two-phase layout for large graphs (>5000 nodes).

    Phase 1: Simulate the top-5000 most important nodes.
    Phase 2: Place remaining nodes near their most-connected simulated neighbor.
    """
    indexed = [(i, n) for i, n in enumerate(nodes)]
    indexed.sort(key=lambda x: x[1].get("importance", 0), reverse=True)

    subsample_ids = {indexed[i][1]["id"] for i in range(min(_SUBSAMPLE_SIZE, len(indexed)))}
    sub_nodes = [n for n in nodes if n["id"] in subsample_ids]
    sub_edges = [
        e for e in edges
        if e["source"] in subsample_ids and e["target"] in subsample_ids
    ]

    positions = _simulate(sub_nodes, sub_edges, iterations=iterations, seed=seed)

    # Phase 2: place remaining nodes near their most-connected simulated neighbor
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        adj[e["source"]].append(e["target"])
        adj[e["target"]].append(e["source"])

    rng = np.random.RandomState(seed + 1)

    for node in nodes:
        nid = node["id"]
        if nid in positions:
            continue

        neighbors = adj.get(nid, [])
        best_neighbor = None
        best_count = -1
        for nb in neighbors:
            if nb in positions:
                count = len(adj.get(nb, []))
                if count > best_count:
                    best_count = count
                    best_neighbor = nb

        if best_neighbor is not None:
            ref = positions[best_neighbor]
            positions[nid] = {
                "x": round(ref["x"] + rng.normal(0, 25), 1),
                "y": round(ref["y"] + rng.normal(0, 25), 1),
            }
        else:
            group = node.get("group", "")
            group_pos = [
                positions[n["id"]] for n in sub_nodes
                if n.get("group") == group and n["id"] in positions
            ]
            if group_pos:
                avg_x = sum(p["x"] for p in group_pos) / len(group_pos)
                avg_y = sum(p["y"] for p in group_pos) / len(group_pos)
                positions[nid] = {
                    "x": round(avg_x + rng.normal(0, 30), 1),
                    "y": round(avg_y + rng.normal(0, 30), 1),
                }
            else:
                positions[nid] = {
                    "x": round(rng.normal(0, 200), 1),
                    "y": round(rng.normal(0, 200), 1),
                }

    return positions
