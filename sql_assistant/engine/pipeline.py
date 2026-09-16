"""
Unified Text-to-SQL Pipeline orchestrator.
Chains: Greeting Guardrail -> Retrieval -> Groq Prompting -> Security Validation -> Execution.
"""
import time
import logging
from typing import Dict, Any, Optional

from .db_manager import get_database_engine, extract_live_metadata
from .retrieval import run_retrieval_pipeline
from .groq_service import build_prompt, call_groq, parse_structured_output
from .security_guardrail import execute_safe_query
from .dynamic_linker import get_table_adjacency_graph

logger = logging.getLogger("sql_assistant.pipeline")

# Instant conversational greetings guardrail (0 token consumption)
GREETINGS = {
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "help", "who are you", "thanks", "thank you",
    "what can you do", "hi there"
}


def text_to_sql(question: str, validate: bool = True, backend: str = "groq") -> Dict[str, Any]:
    """
    Main Text-to-SQL entry point for bank staff queries.
    Returns:
    {
        "question": str,
        "is_chit_chat": bool,
        "generated_sql": Optional[str],
        "explanation": str,
        "detected_domain": Optional[str],
        "candidate_tables": List[str],
        "database_target": str,
        "dialect": str,
        "elapsed_time_ms": float,
        "validation": {
            "success": bool,
            "columns": List[str],
            "rows_count": int,
            "rows": List[List[Any]],
            "error": Optional[str]
        }
    }
    """
    start_time = time.time()
    q_clean = question.strip().lower()

    # 1. 🛡️ Conversational / Greeting Guardrail
    if q_clean in GREETINGS:
        elapsed = round((time.time() - start_time) * 1000, 2)
        return {
            "question": question,
            "is_chit_chat": True,
            "generated_sql": None,
            "explanation": "Hello! I am your Bank SQL Assistant. Ask me a question about accounts, loans, transactions, cards, or clients!",
            "detected_domain": "General Conversation",
            "candidate_tables": [],
            "database_target": "N/A",
            "dialect": "tsql",
            "elapsed_time_ms": elapsed,
            "validation": {
                "success": True,
                "columns": [],
                "rows_count": 0,
                "rows": [],
                "error": None
            }
        }

    # 2. Database & Schema Discovery
    engine, dialect, db_name = get_database_engine()

    # 3. Retrieval Pipeline (Intent -> Tables -> Columns)
    retrieval_res = run_retrieval_pipeline(question, top_k_tables=5, max_cols_per_table=8)
    pruned_schema = retrieval_res["pruned_schema"]
    detected_domain = retrieval_res["detected_domain"]
    candidate_tables = retrieval_res["candidate_tables"]

    # 4. Prompt Assembly & Groq Call
    messages = build_prompt(question, pruned_schema)
    raw_output = call_groq(messages)
    parsed = parse_structured_output(raw_output)

    generated_sql = parsed.get("generated_sql")
    explanation = parsed.get("explanation", "")

    # 5. Execution & Validation
    validation_result: Dict[str, Any] = {
        "success": False,
        "columns": [],
        "rows_count": 0,
        "rows": [],
        "error": None
    }

    if validate and generated_sql:
        validation_result = execute_safe_query(generated_sql, engine, dialect=dialect)
    elif not generated_sql:
        # LLM flagged chit-chat or invalid query according to system instructions
        validation_result["success"] = True

    elapsed = round((time.time() - start_time) * 1000, 2)

    return {
        "question": question,
        "is_chit_chat": not bool(generated_sql),
        "generated_sql": generated_sql,
        "explanation": explanation,
        "detected_domain": detected_domain,
        "candidate_tables": candidate_tables,
        "database_target": db_name,
        "dialect": dialect,
        "elapsed_time_ms": elapsed,
        "validation": validation_result
    }


def get_pipeline_engine():
    """Returns the active database engine, dialect, and name."""
    return get_database_engine()


def get_schema_summary() -> Dict[str, Any]:
    """
    Returns structured database schema summary for the Schema Explorer modal:
    - Tables with column details (type, PK, FK)
    - Relationship graph
    - Sample preview rows
    """
    catalog = extract_live_metadata()
    graph = get_table_adjacency_graph()
    _, dialect, db_name = get_database_engine()

    summary_tables = []
    for tbl, meta in catalog.items():
        summary_tables.append({
            "name": tbl,
            "columns": meta.get("columns", []),
            "primary_key": meta.get("primary_key", []),
            "foreign_keys": meta.get("foreign_keys", []),
            "sample_rows": meta.get("sample_rows", []),
            "related_tables": graph.get(tbl.lower(), [])
        })

    return {
        "database_name": db_name,
        "dialect": dialect,
        "total_tables": len(summary_tables),
        "tables": summary_tables,
        "relationship_graph": graph
    }

