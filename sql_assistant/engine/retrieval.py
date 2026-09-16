"""
Retrieval module: Intent Classification, Table Retrieval with Bridge Discovery, and Column Pruning.
"""
import re
from typing import Dict, List, Any, Optional
from .db_manager import extract_live_metadata
from .dynamic_linker import get_table_adjacency_graph

from .config import get_business_glossary


def get_dynamic_domains() -> List[tuple]:
    """Loads domains dynamically from business_glossary.yaml if available."""
    glossary = get_business_glossary()
    custom_domains = glossary.get("domains", {})
    if custom_domains:
        result = []
        for d_name, d_info in custom_domains.items():
            desc = d_info.get("description", "") if isinstance(d_info, dict) else str(d_info)
            kw = set(re.findall(r'[a-z0-9]+', desc.lower()))
            if isinstance(d_info, dict) and "tables" in d_info:
                kw.update(t.lower() for t in d_info["tables"])
            result.append((d_name, list(kw)))
        return result
    return []


def intent_agent(question: str, candidate_tables: Optional[List[str]] = None) -> str:
    """Classifies the natural language question into a business domain dynamically."""
    domains = get_dynamic_domains()
    if domains:
        q_tokens = set(re.findall(r'[a-z0-9]+', question.lower()))
        best_domain, max_score = None, -1

        for domain_name, keywords in domains:
            score = sum(1 for kw in keywords if kw in q_tokens or any(kw in tok for tok in q_tokens))
            if score > max_score and score > 0:
                max_score = score
                best_domain = domain_name

        if best_domain:
            return best_domain

    # Dynamic fallback: derived automatically from candidate table names
    if candidate_tables:
        clean_names = [t.replace('_', ' ').capitalize() for t in candidate_tables[:2]]
        return " & ".join(clean_names)

    return "General Data Query"


def table_retrieval_agent(question: str, top_k: int = 5) -> List[str]:
    """
    Identifies relevant tables for the query using:
    1. Direct table name matching.
    2. Dynamic column name keyword matching.
    3. Glossary synonyms (e.g. 'deposit' -> trans, 'customer' -> client).
    4. Dynamic bridge table expansion (e.g. client + card -> adds disp).
    """
    catalog = extract_live_metadata()
    all_tables = list(catalog.keys())
    tables_map = {t.lower(): t for t in all_tables}

    q_lower = question.lower()
    q_tokens = set(re.findall(r'[a-z0-9]+', q_lower))
    matched: set[str] = set()

    # 1. Direct Table Name Matching (handles plurals too, e.g. loans -> loan)
    for t_lower, orig in tables_map.items():
        if re.search(rf"\b{re.escape(t_lower)}s?\b", q_lower):
            matched.add(orig)

    # 2. Dynamic Column Name Matching (matches words like 'amount', 'balance', 'gender', 'payments')
    for tbl, meta in catalog.items():
        for c in meta.get("columns", []):
            c_name = c.get("column_name", "").lower()
            if len(c_name) > 2 and (c_name in q_tokens or any(token.startswith(c_name) for token in q_tokens)):
                matched.add(tbl)

    # 3. Dynamic Synonyms from business_glossary.yaml (if user uses business slang)
    glossary = get_business_glossary()
    synonyms = glossary.get("synonyms", {})
    for tbl_target, slang_list in synonyms.items():
        if any(slang.lower() in q_lower for slang in slang_list):
            if tbl_target.lower() in tables_map:
                matched.add(tables_map[tbl_target.lower()])

    # 4. Dynamic Bridge Linker (Auto-discovers intermediate bridge tables connecting entities)
    graph = get_table_adjacency_graph()
    matched_lowers = [m.lower() for m in matched]
    for i in range(len(matched_lowers)):
        for j in range(i + 1, len(matched_lowers)):
            t1, t2 = matched_lowers[i], matched_lowers[j]
            bridges = set(graph.get(t1, [])) & set(graph.get(t2, []))
            for b in bridges:
                matched.add(tables_map.get(b, b))

    # Fallback to all tables if nothing matched
    res = list(matched) if matched else all_tables
    return res[:top_k]


def column_prune_agent(question: str, candidate_tables: List[str], max_cols_per_table: int = 8) -> Dict[str, List[Dict[str, Any]]]:
    """
    Prunes columns to keep only those relevant to the question while
    strictly preserving primary and foreign keys for joins.
    """
    catalog = extract_live_metadata()
    q_tokens = set(re.findall(r'[a-z0-9]+', question.lower()))
    pruned_schema: Dict[str, List[Dict[str, Any]]] = {}

    for table in candidate_tables:
        meta = catalog.get(table)
        if not meta:
            for k, v in catalog.items():
                if k.lower() == table.lower():
                    meta = v
                    table = k
                    break
        if not meta or "columns" not in meta:
            continue

        kept, rest = [], []
        for col in meta["columns"]:
            col_name = col.get("column_name") or col.get("name", "")
            is_key = col.get("is_primary_key") or col.get("is_foreign_key")

            col_tokens = set(re.findall(r'[a-z0-9]+', col_name.lower()))
            overlap = len(q_tokens & col_tokens)

            col_info = {
                "column_name": col_name,
                "data_type": col.get("data_type") or "TEXT",
                "is_primary_key": col.get("is_primary_key", False),
                "is_foreign_key": col.get("is_foreign_key", False),
                "references": col.get("references"),
            }

            if is_key:
                kept.append({**col_info, "keep_reason": "key"})
            elif overlap > 0:
                kept.append({**col_info, "keep_reason": f"match:{overlap}"})
            else:
                rest.append({**col_info, "keep_reason": "context"})

        remaining_slots = max_cols_per_table - len(kept)
        if remaining_slots > 0:
            for c in rest[:remaining_slots]:
                kept.append(c)

        pruned_schema[table] = kept

    return pruned_schema


def run_retrieval_pipeline(question: str, top_k_tables: int = 5, max_cols_per_table: int = 8) -> Dict[str, Any]:
    """
    Chains Intent -> Table Retrieval -> Column Pruning.
    Returns dictionary ready for prompt assembly.
    """
    candidate_tables = table_retrieval_agent(question, top_k=top_k_tables)
    detected_domain = intent_agent(question, candidate_tables=candidate_tables)
    pruned_schema = column_prune_agent(question, candidate_tables, max_cols_per_table=max_cols_per_table)

    return {
        "question": question,
        "detected_domain": detected_domain,
        "candidate_tables": candidate_tables,
        "pruned_schema": pruned_schema,
    }

