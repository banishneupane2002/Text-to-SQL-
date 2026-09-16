"""
Security Guardrail and Safe Execution Engine for Microsoft SQL Server (T-SQL).
Enforces read-only SELECT queries and blocks destructive DDL/DML operations using sqlglot.
"""
import re
import logging
from typing import Dict, Any, List
import sqlglot
from sqlglot import exp
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

logger = logging.getLogger("sql_assistant.security_guardrail")


def validate_and_secure_tsql(sql: str) -> str:
    """
    Validates query against Microsoft SQL Server (T-SQL) rules and enforces read-only safety.
    Raises ValueError on syntax errors or policy violations.
    """
    if not sql or not sql.strip():
        raise ValueError("Empty SQL query provided.")

    # Strip markdown code fences
    clean = re.sub(r"^```(sql|tsql|json)?|```$", "", sql.strip(), flags=re.MULTILINE).strip()

    # Parse using T-SQL dialect
    try:
        tree = sqlglot.parse_one(clean, read="tsql")
    except Exception as e:
        raise ValueError(f"T-SQL Syntax Error: {e}")

    if not isinstance(tree, exp.Select):
        raise ValueError("Security Policy Violation: Only SELECT queries are permitted.")

    # Block destructive statements
    FORBIDDEN_COMMANDS = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter)
    for node in tree.walk():
        node_obj = node[0] if isinstance(node, tuple) else node
        if isinstance(node_obj, FORBIDDEN_COMMANDS):
            cmd_name = type(node_obj).__name__.upper()
            raise ValueError(f"Security Policy Violation: Modification command '{cmd_name}' is strictly forbidden.")

    return clean


def execute_safe_query(generated_sql: str, engine: Engine, dialect: str = "tsql", preview_limit: int = 100) -> Dict[str, Any]:
    """
    Executes the validated query safely:
    - On Microsoft SQL Server: executes pure T-SQL directly.
    - On SQLite Fallback: transpiles T-SQL -> SQLite dialect for live preview.
    """
    try:
        # 1. Strict T-SQL validation
        safe_tsql = validate_and_secure_tsql(generated_sql)

        # 2. Determine execution dialect syntax
        if dialect == "sqlite":
            try:
                exec_sql = sqlglot.transpile(safe_tsql, read="tsql", write="sqlite")[0]
            except Exception as transpile_err:
                logger.warning(f"Transpile error ({transpile_err}); falling back to clean SQL.")
                exec_sql = safe_tsql
        else:
            exec_sql = safe_tsql

        # 3. Safe Execution
        with engine.connect() as conn:
            res = conn.execute(sa_text(exec_sql))
            rows = res.fetchmany(preview_limit)
            columns = list(res.keys())

            # Serialize values (dates, decimals, UUIDs) cleanly for JSON
            formatted_rows: List[List[Any]] = []
            for row in rows:
                formatted_rows.append([
                    str(v) if v is not None and not isinstance(v, (int, float, bool, str)) else v
                    for v in row
                ])

            return {
                "success": True,
                "safe_sql": safe_tsql,
                "executed_sql": exec_sql,
                "columns": columns,
                "rows_count": len(formatted_rows),
                "rows": formatted_rows,
                "error": None
            }

    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return {
            "success": False,
            "safe_sql": generated_sql,
            "executed_sql": generated_sql,
            "columns": [],
            "rows_count": 0,
            "rows": [],
            "error": str(e)
        }

