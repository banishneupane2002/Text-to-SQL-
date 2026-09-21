"""
Self-Healing SQL Query Repair Module:
Catches runtime database errors (e.g. invalid column names, syntax errors, type mismatches),
inspects the live database catalog for the actual referenced tables,
and automatically repairs the query via a compact, targeted LLM correction pass.
100% database-agnostic: derives all columns and schemas dynamically from live metadata.
"""
import re
import logging
from typing import Dict, List, Any, Optional, Set, Tuple

from .db_manager import extract_live_metadata
from .security_guardrail import execute_safe_query
from .groq_service import call_groq, parse_structured_output

logger = logging.getLogger("sql_assistant.self_healing")


def extract_tables_from_sql(sql_str: str, catalog: Dict[str, Any]) -> List[str]:
    """
    Extracts table names referenced in an SQL query (FROM / JOIN clauses)
    and maps them to matching keys in the live database catalog.
    Supports [Schema].[Table], Schema.Table, [Table], and Table.
    """
    if not sql_str:
        return []

    # Map catalog keys for case-insensitive lookup
    # Both full "schema.table" and unqualified "table"
    catalog_full_map = {k.lower(): k for k in catalog.keys()}
    catalog_unqualified_map = {k.lower().split(".")[-1]: k for k in catalog.keys()}

    found_tables: Set[str] = set()

    # Pattern for FROM / JOIN clauses
    # Handles: [Schema].[Table], Schema.Table, [Table], Table
    table_pattern = re.compile(
        r'(?:FROM|JOIN)\s+(?:\[?([a-zA-Z0-9_]+)\]?\.)?\[?([a-zA-Z0-9_]+)\]?',
        re.IGNORECASE
    )

    matches = table_pattern.findall(sql_str)
    for schema_part, table_part in matches:
        if schema_part and table_part:
            full_ref = f"{schema_part}.{table_part}".lower()
            if full_ref in catalog_full_map:
                found_tables.add(catalog_full_map[full_ref])
                continue
        if table_part:
            t_low = table_part.lower()
            if t_low in catalog_unqualified_map:
                found_tables.add(catalog_unqualified_map[t_low])
            elif t_low in catalog_full_map:
                found_tables.add(catalog_full_map[t_low])

    return list(found_tables)


def build_compact_schema_summary(tables: List[str], catalog: Dict[str, Any]) -> str:
    """Builds a compact, token-efficient schema representation for the repair prompt."""
    lines = []
    for tbl in tables:
        meta = catalog.get(tbl, {})
        cols = meta.get("columns", [])
        col_strs = []
        for c in cols:
            name = c.get("column_name") or c.get("name", "")
            dtype = c.get("data_type", "")
            pk = " (PK)" if c.get("is_primary_key") else ""
            fk = f" (FK -> {c.get('references')})" if c.get("is_foreign_key") and c.get("references") else ""
            col_strs.append(f"{name} [{dtype}]{pk}{fk}")
        lines.append(f"Table: {tbl}\n  Columns: {', '.join(col_strs)}")
    return "\n\n".join(lines)


def heal_failed_query(
    question: str,
    failed_sql: str,
    error_message: str,
    engine: Any,
    dialect: str = "tsql",
    catalog: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """
    Attempts to repair a failed SQL query using runtime database error feedback.
    Returns: (healed_success, repaired_sql, explanation, validation_result)
    """
    if not failed_sql or not error_message:
        return False, None, None, None

    if catalog is None:
        catalog = extract_live_metadata()

    # 1. Identify all tables referenced in the failed query
    referenced_tables = extract_tables_from_sql(failed_sql, catalog)

    if not referenced_tables:
        # Fallback: if regex missed tables, take top 4 tables from catalog
        referenced_tables = list(catalog.keys())[:4]

    logger.info(f"Self-Healing: Triggered for error: {error_message[:100]}... Tables involved: {referenced_tables}")

    # 2. Build accurate live schema for the involved tables
    schema_context = build_compact_schema_summary(referenced_tables, catalog)

    # 3. Clean up the error message (remove lengthy driver URLs or internal stack traces)
    clean_error = error_message
    if "Background on this error at" in clean_error:
        clean_error = clean_error.split("Background on this error at")[0].strip()
    if len(clean_error) > 400:
        clean_error = clean_error[:400] + "..."

    # 4. Construct high-priority repair prompt
    system_msg = (
        f"You are an automated SQL repair agent for a {dialect.upper()} database. "
        "The previous query failed with a database execution error. "
        "Use the EXACT column and table names provided in the REAL DATABASE SCHEMA to fix the error. "
        "Do NOT invent or guess column names. "
        "Return ONLY a single valid JSON object in this exact format: "
        '{"generated_sql": "SELECT ...;", "explanation": "Fixed column/syntax error: ..."}'
    )

    user_msg = (
        f"USER QUESTION: {question}\n\n"
        f"FAILED SQL:\n{failed_sql}\n\n"
        f"DATABASE EXECUTION ERROR:\n{clean_error}\n\n"
        f"REAL DATABASE SCHEMA:\n{schema_context}\n\n"
        "Provide the corrected, executable SQL query:"
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    try:
        raw_output = call_groq(messages)
        parsed = parse_structured_output(raw_output)
        repaired_sql = parsed.get("generated_sql")
        explanation = parsed.get("explanation", "Query automatically repaired based on live database schema.")

        if not repaired_sql:
            logger.warning("Self-Healing: Model returned no SQL.")
            return False, None, None, None

        logger.info(f"Self-Healing: Testing repaired SQL: {repaired_sql}")

        # 5. Execute repaired query to verify it actually works
        validation_result = execute_safe_query(repaired_sql, engine, dialect=dialect)

        if validation_result.get("success") is True:
            logger.info("Self-Healing: Query repaired and executed successfully!")
            return True, repaired_sql, explanation, validation_result
        else:
            logger.warning(f"Self-Healing: Repaired query failed: {validation_result.get('error')}")
            return False, repaired_sql, explanation, validation_result

    except Exception as exc:
        logger.error(f"Self-Healing: Exception during repair pass: {exc}")
        return False, None, None, None
