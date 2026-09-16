"""
Dynamic foreign key linker and table relationship graph builder.
Discovers relationships from foreign keys and shared identity columns.
"""
from typing import Dict, List, Set
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
            if ref:
                ref_low = ref.lower()
                graph[t_low].add(ref_low)
                if ref_low not in graph:
                    graph[ref_low] = set()
                graph[ref_low].add(t_low)

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

    # 2. Fallback: Common ID columns if no formal foreign keys found
    if not relations and catalog:
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

