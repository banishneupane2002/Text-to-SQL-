"""
Retrieval module: Intent Classification, Evidence-Scored Table Retrieval with Multi-Hop Bridge Discovery, and Column Pruning.
100% dynamic: derives all scores and relationships from live database catalog metadata.
"""
import re
from typing import Dict, List, Any, Optional, Set
from .db_manager import extract_live_metadata
from .dynamic_linker import get_table_adjacency_graph, get_multihop_bridges
from .config import get_business_glossary


GENERIC_COLUMN_WORDS = {"id", "date", "type", "status", "amount", "name", "code", "desc", "val", "num"}


def tokenize_identifier(name: str) -> List[str]:
    """Tokenizes identifiers supporting snake_case, PascalCase, camelCase, and punctuation."""
    tokens = re.findall(r'[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)', name)
    if not tokens:
        tokens = [w for w in re.split(r'[^a-zA-Z0-9]+', name) if w]
    return [t.lower() for t in tokens if len(t) > 1]


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




def table_retrieval_agent(question: str, max_tables: int = 6) -> List[str]:
    """
    Identifies relevant tables dynamically using:
    1. Evidence-based relevance scoring (direct table names, multi-token columns, synonyms).
    2. Dynamic BFS multi-hop bridge discovery to connect all core entities.
    3. Core table protection: guarantees primary tables and bridges are never dropped.
    """
    catalog = extract_live_metadata()
    all_tables = list(catalog.keys())
    tables_map = {t.lower(): t for t in all_tables}

    q_lower = question.lower()
    q_tokens = set(re.findall(r'[a-z0-9]+', q_lower))

    glossary = get_business_glossary()
    synonyms = glossary.get("synonyms", {}) if isinstance(glossary, dict) else {}

    scores: Dict[str, float] = {t: 0.0 for t in all_tables}

    for orig_tbl in all_tables:
        t_low = orig_tbl.lower()
        t_unqualified = t_low.split(".")[-1]
        meta = catalog.get(orig_tbl, {})

        # 1. Direct Table Name Match (handles singular and plural, e.g. 'products' -> 'product')
        # High confidence signal (+10.0)
        if re.search(rf"\b{re.escape(t_low)}s?\b", q_lower) or re.search(rf"\b{re.escape(t_unqualified)}s?\b", q_lower):
            scores[orig_tbl] += 10.0

        # 2. Dynamic Synonym Match from decoupled glossary (if present) (+8.0)
        matched_syn = False
        for lookup_key in (t_low, t_unqualified):
            if lookup_key in synonyms:
                for slang in synonyms[lookup_key]:
                    if slang.lower() in q_lower:
                        scores[orig_tbl] += 8.0
                        matched_syn = True
                        break
            if matched_syn:
                break

        # 3. Column Keyword & Phrase Matching
        for col in meta.get("columns", []):
            c_name = col.get("column_name") or col.get("name") or ""
            col_words = tokenize_identifier(c_name)
            if not col_words:
                continue

            # Multi-token column match (e.g. "date of birth" containing both 'birth' and 'date')
            matching_words = [w for w in col_words if w in q_tokens or any(w in t for t in q_tokens)]

            if len(col_words) > 1 and len(matching_words) == len(col_words):
                # Full multi-token match on composite column (e.g. birth_date, ListPrice)
                scores[orig_tbl] += 6.0
            elif matching_words:
                for w in matching_words:
                    if w in GENERIC_COLUMN_WORDS:
                        # Low weight for generic words like 'date' or 'type' to avoid false positives
                        scores[orig_tbl] += 0.5
                    else:
                        # Specific domain words like 'balance', 'payments', 'gender'
                        scores[orig_tbl] += 2.0

    # Separate high-evidence Core Tables (score >= 4.0) from lower scoring tables
    core_tables = [tbl for tbl, sc in scores.items() if sc >= 4.0]

    # If no core tables found, take any tables with positive score
    if not core_tables:
        core_tables = [tbl for tbl, sc in scores.items() if sc > 0.0]

    # If still no tables scored, return all tables (capped)
    if not core_tables:
        return all_tables[:max_tables]

    # Dynamically find BFS Multi-Hop Bridge Tables required to connect core tables
    bridge_names_low = get_multihop_bridges(core_tables)
    bridge_tables = [tables_map[b] for b in bridge_names_low if b in tables_map and tables_map[b] not in core_tables]

    # Combine: Core tables + Bridge tables (preserved with highest priority)
    selected_tables = list(core_tables)
    for b in bridge_tables:
        if b not in selected_tables:
            selected_tables.append(b)

    # If space permits, add remaining tables ordered by score
    if len(selected_tables) < max_tables:
        remaining = [
            tbl for tbl, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if tbl not in selected_tables and sc > 0.0
        ]
        for r in remaining:
            if len(selected_tables) >= max_tables:
                break
            selected_tables.append(r)

    return selected_tables


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

        scored_cols = []
        for col in meta["columns"]:
            col_name = col.get("column_name") or col.get("name", "")
            is_key = col.get("is_primary_key") or col.get("is_foreign_key")

            col_words = tokenize_identifier(col_name)
            overlap = len(set(col_words) & q_tokens)

            col_info = {
                "column_name": col_name,
                "data_type": col.get("data_type") or "TEXT",
                "is_primary_key": col.get("is_primary_key", False),
                "is_foreign_key": col.get("is_foreign_key", False),
                "references": col.get("references"),
            }

            if is_key:
                score = 100.0  # Always keep keys
                reason = "key"
            elif len(col_words) > 1 and overlap == len(col_words):
                score = 50.0 + overlap
                reason = f"phrase_match:{overlap}"
            elif overlap > 0:
                score = 10.0 + overlap
                reason = f"match:{overlap}"
            else:
                score = 0.0
                reason = "context"

            scored_cols.append((score, {**col_info, "keep_reason": reason}))

        # Sort columns by relevance score descending
        scored_cols.sort(key=lambda x: x[0], reverse=True)
        kept = [c[1] for c in scored_cols[:max_cols_per_table]]
        pruned_schema[table] = kept

    return pruned_schema


def run_retrieval_pipeline(question: str, top_k_tables: int = 6, max_cols_per_table: int = 8) -> Dict[str, Any]:
    """
    Chains Intent -> Table Retrieval -> Column Pruning.
    Returns dictionary ready for prompt assembly.
    """
    candidate_tables = table_retrieval_agent(question, max_tables=top_k_tables)
    detected_domain = intent_agent(question, candidate_tables=candidate_tables)
    pruned_schema = column_prune_agent(question, candidate_tables, max_cols_per_table=max_cols_per_table)

    return {
        "question": question,
        "detected_domain": detected_domain,
        "candidate_tables": candidate_tables,
        "pruned_schema": pruned_schema,
    }

