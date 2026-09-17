"""
Dynamic foreign key linker and table relationship graph builder.
Discovers relationships from foreign keys and shared identity columns,
and provides multi-hop BFS pathfinding to connect any pair of tables.
"""
from collections import deque
from typing import Dict, List, Set, Optional
from .db_manager import extract_live_metadata


def get_table_adjacency_graph() -> Dict[str, List[str]]:
    """
    Dynamically builds table relationship graph using foreign keys and shared IDs.
    Returns: { 'client': ['disp', 'district'], 'disp': ['client', 'account', 'card'], ... }
    """
    catalog = extract_live_metadata()
    all_tables = list(catalog.keys())
    graph: Dict[str, Set[str]] = {t.lower(): set() for t in all_tables}

    # 1. From Foreign Keys in Catalog
    for tbl, meta in catalog.items():
        t_low = tbl.lower()
        for fk in meta.get("foreign_keys", []):
            ref = fk.get("referred_table")
            ref_schema = fk.get("referred_schema")
            if ref:
                ref_low = ref.lower()
                graph[t_low].add(ref_low)
                if ref_low not in graph:
                    graph[ref_low] = set()
                graph[ref_low].add(t_low)
                candidates = [ref_low]
                if ref_schema and ref_schema.lower() != "dbo":
                    candidates.append(f"{ref_schema.lower()}.{ref_low}")
                for cand in candidates:
                    graph[t_low].add(cand)
                    if cand not in graph:
                        graph[cand] = set()
                    graph[cand].add(t_low)

    # 2. From Shared _id Columns (fallback / implicit relationship discovery)
    cols_map = {}
    for tbl, meta in catalog.items():
        cols_map[tbl.lower()] = {
            c.get("column_name", "").lower()
            for c in meta.get("columns", [])
            if c.get("column_name")
        }

    t_list = list(cols_map.keys())
    for i in range(len(t_list)):
        for j in range(i + 1, len(t_list)):
            t1, t2 = t_list[i], t_list[j]
            shared_ids = {
                c for c in (cols_map[t1] & cols_map[t2])
                if c.endswith("_id") or c == "id"
            }
            if shared_ids:
                graph[t1].add(t2)
                graph[t2].add(t1)

    return {k: sorted(list(v)) for k, v in graph.items()}


def find_shortest_path(graph: Dict[str, List[str]], start: str, end: str) -> List[str]:
    """
    Finds the shortest path between two tables using Breadth-First Search (BFS).
    Returns list of table names in path, e.g. ['client', 'disp', 'account', 'loan'].
    """
    start_low = start.lower()
    end_low = end.lower()
    if start_low == end_low:
        return [start_low]
    if start_low not in graph or end_low not in graph:
        return []

    queue = deque([[start_low]])
    visited = {start_low}

    while queue:
        path = queue.popleft()
        node = path[-1]
        for neighbor in graph.get(node, []):
            if neighbor == end_low:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])
    return []


def get_multihop_bridges(candidate_tables: List[str]) -> Set[str]:
    """
    Finds all intermediate bridge tables required to connect all pairs of candidate tables.
    Returns set of bridge table names (in lowercase).
    """
    if len(candidate_tables) < 2:
        return set()

    graph = get_table_adjacency_graph()
    bridges: Set[str] = set()
    cand_lowers = [t.lower() for t in candidate_tables]

    for i in range(len(cand_lowers)):
        for j in range(i + 1, len(cand_lowers)):
            t1, t2 = cand_lowers[i], cand_lowers[j]
            path = find_shortest_path(graph, t1, t2)
            # Intermediate tables in path (excluding endpoints t1 and t2)
            if len(path) > 2:
                for b in path[1:-1]:
                    bridges.add(b)

    return bridges


def get_dynamic_relationships(candidate_tables: List[str] = None) -> List[str]:
    """
    Dynamically discovers join relationships from database metadata.
    Returns formatted join condition strings like:
      "[client].[district_id] = [district].[district_id]"
    """
    catalog = extract_live_metadata()
    relations: List[str] = []
    seen: Set[str] = set()

    candidate_lowers = [t.lower() for t in candidate_tables] if candidate_tables else None

    # 1. From catalog foreign keys
    for tbl, meta in catalog.items():
        if candidate_lowers and tbl.lower() not in candidate_lowers:
            continue
        for fk in meta.get("foreign_keys", []):
            referred_tbl = fk.get("referred_table")
            if candidate_lowers and referred_tbl and referred_tbl.lower() not in candidate_lowers:
                continue
            for loc_col, rem_col in zip(fk.get("constrained_columns", []), fk.get("referred_columns", [])):
                rel_str = f"[{tbl}].[{loc_col}] = [{referred_tbl}].[{rem_col}]"
                rev_str = f"[{referred_tbl}].[{rem_col}] = [{tbl}].[{loc_col}]"
                if rel_str not in seen and rev_str not in seen:
                    relations.append(rel_str)
                    seen.add(rel_str)

    # 2. Augment / Fallback: Common ID columns if pairs lack formal foreign keys
    cols_by_table = {}
    for tbl, meta in catalog.items():
        if candidate_lowers and tbl.lower() not in candidate_lowers:
            continue
        cols_by_table[tbl] = {
            c.get("column_name", "").lower(): c.get("column_name")
            for c in meta.get("columns", [])
        }

    tbl_names = list(cols_by_table.keys())
    for i in range(len(tbl_names)):
        for j in range(i + 1, len(tbl_names)):
            t1, t2 = tbl_names[i], tbl_names[j]
            # Check if this pair already has a relationship recorded
            pair_already_connected = any(
                f"[{t1}]" in r and f"[{t2}]" in r for r in relations
            )
            if not pair_already_connected:
                common_ids = set(cols_by_table[t1].keys()) & set(cols_by_table[t2].keys())
                for cid in common_ids:
                    if cid.endswith("_id") or cid == "id":
                        c1 = cols_by_table[t1][cid]
                        c2 = cols_by_table[t2][cid]
                        rel_str = f"[{t1}].[{c1}] = [{t2}].[{c2}]"
                        rev_str = f"[{t2}].[{c2}] = [{t1}].[{c1}]"
                        if rel_str not in seen and rev_str not in seen:
                            relations.append(rel_str)
                            seen.add(rel_str)

    return relations
