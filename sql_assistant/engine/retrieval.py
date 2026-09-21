"""
Retrieval module: Intent Classification, Evidence-Scored Table Retrieval with Multi-Hop Bridge Discovery, and Column Pruning.
100% dynamic: derives all scores and relationships from live database catalog metadata.
"""
import re
import json
import logging
from typing import Dict, List, Any, Optional, Set
from .db_manager import extract_live_metadata
from .dynamic_linker import get_table_adjacency_graph, get_multihop_bridges
from .config import get_business_glossary

logger = logging.getLogger("sql_assistant.retrieval")


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


def semantic_table_router(question: str, all_tables: List[str], max_tables: int = 3) -> List[str]:
    """
    Conditional Fallback Router: When keyword matching finds no core tables (e.g. unknown slang,
    zero-glossary setup, or new database), uses LLM semantic reasoning to select
    the 1 to 3 most relevant table names.
    Lightweight, fast, and token-efficient (~200 tokens).
    """
    if not all_tables or not question:
        return []

    tables_display = ", ".join(all_tables)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert database router. Given a user query and a list of database tables, "
                f"select between 1 and {max_tables} table names strictly necessary to answer the query. "
                "Output ONLY a single valid JSON object in this exact format: "
                '{"tables": ["Table1", "Table2"]}. If completely unrelated, output {"tables": []}.'
            )
        },
        {
            "role": "user",
            "content": f"USER QUERY: {question}\n\nAVAILABLE TABLES:\n{tables_display}"
        }
    ]

    try:
        from .groq_service import call_groq
        raw = call_groq(messages)
        clean = raw.strip()
        clean = re.sub(r"^```(json)?|```$", "", clean, flags=re.MULTILINE).strip()
        parsed_data = None
        try:
            parsed_data = json.loads(clean)
        except Exception:
            match = re.search(r'\{.*?\}', clean, flags=re.DOTALL)
            if match:
                try:
                    parsed_data = json.loads(match.group(0))
                except Exception:
                    pass

        tables_list = []
        if isinstance(parsed_data, dict) and "tables" in parsed_data:
            tables_list = parsed_data["tables"]
        elif isinstance(parsed_data, list):
            tables_list = parsed_data
        else:
            arr_match = re.search(r'\[.*?\]', clean, flags=re.DOTALL)
            if arr_match:
                try:
                    tables_list = json.loads(arr_match.group(0))
                except Exception:
                    pass

        if isinstance(tables_list, list):
            tables_map = {t.lower(): t for t in all_tables}
            tables_unqual_map = {t.lower().split(".")[-1]: t for t in all_tables}
            valid = []
            for p in tables_list:
                p_str = str(p).strip().lower()
                if p_str in tables_map and tables_map[p_str] not in valid:
                    valid.append(tables_map[p_str])
                elif p_str in tables_unqual_map and tables_unqual_map[p_str] not in valid:
                    valid.append(tables_unqual_map[p_str])
            if valid:
                logger.info(f"Semantic Router matched tables: {valid}")
                return valid[:max_tables]
    except Exception as exc:
        logger.warning(f"Semantic Router fallback error: {exc}")

    return []

SQL_PHRASING_WORDS = {
    "order", "ordered", "ordering", "by", "sort", "sorted", "sorting",
    "desc", "descending", "asc", "ascending", "group", "grouped", "grouping",
    "show", "list", "display", "get", "find", "fetch", "select", "give", "me",
    "all", "each", "every", "both", "any", "having", "where", "with", "their",
    "from", "and", "or", "not", "the", "a", "an", "of", "in", "on", "at", "to", "for"
}


def table_retrieval_agent(question: str, max_tables: int = 10, detected_domain: Optional[str] = None) -> List[str]:
    """
    Identifies relevant tables dynamically using:
    1. Domain-level entity boosting (domain tables get strong priority).
    2. Evidence-based relevance scoring (direct table names, multi-token columns, synonyms).
    3. SQL phrasing filtering (ignores 'order by', 'sorted by', 'show', 'list' false positives).
    4. Dynamic BFS multi-hop bridge discovery to connect all core entities.
    """
    catalog = extract_live_metadata()
    all_tables = list(catalog.keys())
    tables_map = {t.lower(): t for t in all_tables}
    tables_unqual_map = {t.lower().split(".")[-1]: t for t in all_tables}

    q_lower = question.lower()
    raw_tokens = set(re.findall(r'[a-z0-9]+', q_lower))
    clean_q_tokens = {w for w in raw_tokens if w not in SQL_PHRASING_WORDS and len(w) > 1}

    glossary = get_business_glossary()
    synonyms = glossary.get("synonyms", {}) if isinstance(glossary, dict) else {}
    col_synonyms = glossary.get("column_synonyms", {}) if isinstance(glossary, dict) else {}

    domain_tables = set()
    if detected_domain and glossary and "domains" in glossary:
        domains_map = glossary["domains"]
        if detected_domain in domains_map:
            d_info = domains_map[detected_domain]
            if isinstance(d_info, dict) and "tables" in d_info:
                for dt in d_info["tables"]:
                    domain_tables.add(dt.lower())
                    domain_tables.add(dt.lower().split(".")[-1])

    scores: Dict[str, float] = {t: 0.0 for t in all_tables}

    for orig_tbl in all_tables:
        t_low = orig_tbl.lower()
        t_unqualified = t_low.split(".")[-1]
        meta = catalog.get(orig_tbl, {})

        # 0. Domain Boost (+12.0)
        if t_low in domain_tables or t_unqualified in domain_tables:
            scores[orig_tbl] += 12.0

        # 1. Direct Table Name Match (skip pure SQL phrasing words like 'order')
        if t_unqualified not in SQL_PHRASING_WORDS:
            if re.search(rf"\b{re.escape(t_low)}s?\b", q_lower) or re.search(rf"\b{re.escape(t_unqualified)}s?\b", q_lower):
                scores[orig_tbl] += 10.0
            else:
                t_tokens = tokenize_identifier(t_unqualified)
                t_overlap = [w for w in t_tokens if w in clean_q_tokens]
                if t_overlap:
                    scores[orig_tbl] += 3.0 * len(t_overlap)

        # 2. Dynamic Synonym Match from decoupled glossary (+8.0)
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

        # 3. Column Keyword & Phrase Matching + Column Synonyms
        for col in meta.get("columns", []):
            c_name = col.get("column_name") or col.get("name") or ""
            col_words = tokenize_identifier(c_name)
            if not col_words:
                continue

            matching_words = [
                w for w in col_words
                if w in clean_q_tokens or (w + 's') in clean_q_tokens or w.rstrip('s') in clean_q_tokens
            ]

            if len(col_words) > 1 and len(matching_words) == len(col_words):
                scores[orig_tbl] += 6.0
            elif matching_words:
                for w in matching_words:
                    if w in GENERIC_COLUMN_WORDS:
                        scores[orig_tbl] += 0.5
                    else:
                        scores[orig_tbl] += 2.0

            col_low = c_name.lower()
            for col_key in (col_low, *col_words):
                if col_key in col_synonyms:
                    for slang in col_synonyms[col_key]:
                        if slang.lower() in q_lower:
                            scores[orig_tbl] += 5.0
                            break

    # Core tables: tables scoring at least 5.0
    core_tables = [tbl for tbl, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True) if sc >= 5.0]

    # If fast matching found nothing, trigger Semantic Router fallback
    if not core_tables:
        logger.info("Fast keyword matching found 0 core tables. Triggering Semantic Router fallback...")
        routed = semantic_table_router(question, all_tables, max_tables=min(max_tables, 4))
        if routed:
            core_tables = routed

    if not core_tables:
        core_tables = [tbl for tbl, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True) if sc > 0.0][:max_tables]

    if not core_tables:
        return all_tables[:max_tables]

    # Dynamically find BFS Multi-Hop Bridge Tables required to connect core tables
    bridge_names_low = get_multihop_bridges(core_tables[:max_tables])
    bridge_tables = []
    for b in bridge_names_low:
        b_low = b.lower()
        matched = tables_map.get(b_low) or tables_unqual_map.get(b_low)
        if matched and matched not in core_tables and matched not in bridge_tables:
            bridge_tables.append(matched)

    # Combine: Core tables + Bridge tables (preserved with highest priority)
    selected_tables = list(core_tables)
    for b in bridge_tables:
        if b not in selected_tables:
            selected_tables.append(b)

    # Return top max_tables candidates
    return selected_tables[:max_tables]


def column_prune_agent(question: str, candidate_tables: List[str], max_cols_per_table: int = 8) -> Dict[str, List[Dict[str, Any]]]:
    """
    Prunes columns to keep only those relevant to the question while
    strictly preserving primary and foreign keys for joins.
    """
    catalog = extract_live_metadata()
    q_tokens = set(re.findall(r'[a-z0-9]+', question.lower()))
    pruned_schema: Dict[str, List[Dict[str, Any]]] = {}

    glossary = get_business_glossary()
    col_synonyms = glossary.get("column_synonyms", {}) if isinstance(glossary, dict) else {}
    q_lower = question.lower()

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

            # Check column semantic synonyms
            col_low = col_name.lower()
            has_col_syn = False
            for col_key in (col_low, *col_words):
                if col_key in col_synonyms:
                    for slang in col_synonyms[col_key]:
                        if slang.lower() in q_lower:
                            has_col_syn = True
                            break
                if has_col_syn:
                    break

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
            elif has_col_syn:
                score = 60.0
                reason = "col_synonym"
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


def run_retrieval_pipeline(question: str, top_k_tables: int = 10, max_cols_per_table: int = 8) -> Dict[str, Any]:
    """
    Chains Intent -> Table Retrieval (Domain-boosted) -> Column Pruning.
    Returns dictionary ready for prompt assembly.
    """
    detected_domain = intent_agent(question)
    candidate_tables = table_retrieval_agent(question, max_tables=top_k_tables, detected_domain=detected_domain)
    pruned_schema = column_prune_agent(question, candidate_tables, max_cols_per_table=max_cols_per_table)

    return {
        "question": question,
        "detected_domain": detected_domain,
        "candidate_tables": candidate_tables,
        "pruned_schema": pruned_schema,
    }

