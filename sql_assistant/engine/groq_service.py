"""
Groq LLM Service: Dynamic model discovery, prompt assembly, and structured JSON parsing.
Strictly incorporates the updated banking T-SQL system instructions and chit-chat guardrails.
"""
import os
import re
import json
import logging
from typing import Dict, List, Any, Optional

from groq import Groq
from .config import get_groq_api_key, get_groq_model
from .dynamic_linker import get_dynamic_relationships

logger = logging.getLogger("sql_assistant.groq_service")

# --- Updated System Instructions from sql_generator.py ---
SYSTEM_INSTRUCTIONS = """You are an expert banking data analyst who writes precise, safe Microsoft SQL Server (T-SQL) queries.
Rules:
- Dialect: Microsoft SQL Server (T-SQL).
- Row Limiting: Use `SELECT TOP N ...` when a specific limit is requested (e.g. 'top 5'). Default to `SELECT TOP 1000 ...` for unconstrained queries to protect memory. NEVER use `LIMIT`.
- Reserved Keywords: Always wrap reserved table and column names in square brackets, e.g. [order], [trans].
- Schema Qualification: When a table has a schema (e.g. Production.Product), reference it as [Schema].[Table] (e.g. [Production].[Product]). NEVER enclose both schema and table in a single bracket like [Production.Product].
- Joins: Connect tables using their matching primary and foreign keys provided in the KNOWN RELATIONSHIPS (e.g. ON [client].[client_id] = [disp].[client_id]).
- Read-only SELECT statements only. Never write INSERT, UPDATE, DELETE, or DROP.
- Constraints: Follow all user constraints (including negative constraints like 'no need to show gender').
- Return ONLY a single valid JSON object in this exact format:
  {"generated_sql": "SELECT ...;", "explanation": "Short plain English explanation"}

INVALID / CHIT-CHAT INPUT RULE:
- Return {"generated_sql": null, "explanation": "I am your Bank SQL Assistant. Please ask a valid question related to bank accounts, loans, transactions, cards, or clients."} ONLY if the user query is purely a greeting ("hi", "hello"), personal chit-chat ("who are you"), or completely unrelated general trivia ("who won the World Cup?").
- If the user asks for banking or database entities (accounts, clients, loans, transactions, cards, demographics), ALWAYS generate the valid T-SQL query using the provided tables!"""


# Few-shot examples tailored for MS SQL Server
DEFAULT_FEW_SHOTS = [
    {
        "question": "What is the total loan amount and average monthly payment for active loans with status 'A'?",
        "sql": "SELECT SUM([amount]) AS total_loan_amount, AVG([payments]) AS avg_monthly_payment FROM [loan] WHERE [status] = 'A';"
    },
    {
        "question": "Show the top 5 accounts with the largest loan amounts, along with their duration and status.",
        "sql": "SELECT TOP 5 [account].[account_id], [loan].[amount], [loan].[duration], [loan].[status] FROM [loan] JOIN [account] ON [loan].[account_id] = [account].[account_id] ORDER BY [loan].[amount] DESC;"
    },
    {
        "question": "How many female clients have been issued a classic credit card?",
        "sql": "SELECT COUNT(DISTINCT c.[client_id]) AS female_classic_card_clients FROM [client] c JOIN [disp] d ON c.[client_id] = d.[client_id] JOIN [card] cr ON d.[disp_id] = cr.[disp_id] WHERE c.[gender] = 'F' AND cr.[type] = 'classic';"
    },
    {
        "question": "Which 5 districts have the highest number of clients?",
        "sql": "SELECT TOP 5 d.[A2] AS district_name, COUNT(c.[client_id]) AS client_count FROM [client] c JOIN [district] d ON c.[district_id] = d.[district_id] GROUP BY d.[A2] ORDER BY client_count DESC;"
    },
    {
        "question": "Show me the total amount and count of permanent orders for household payments with symbol SIPO.",
        "sql": "SELECT COUNT(*) AS total_orders, SUM([amount]) AS total_sipo_amount FROM [order] WHERE [k_symbol] = 'SIPO';"
    }
]

PREFERRED_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
]

_NON_CHAT_HINTS = ["whisper", "tts", "guard", "safeguard", "moderation", "prompt-guard"]
_GROQ_MODEL_CACHE: Optional[str] = None


def discover_groq_model(client: Groq) -> str:
    """Discovers the best active chat model on the Groq API key."""
    global _GROQ_MODEL_CACHE
    if _GROQ_MODEL_CACHE:
        return _GROQ_MODEL_CACHE

    try:
        models = client.models.list().data
        active_ids = {m.id for m in models if getattr(m, "active", True)}

        for preferred in PREFERRED_GROQ_MODELS:
            if preferred in active_ids:
                _GROQ_MODEL_CACHE = preferred
                return preferred

        candidates = [
            m for m in active_ids
            if not any(hint in m.lower() for hint in _NON_CHAT_HINTS)
        ]
        if candidates:
            _GROQ_MODEL_CACHE = candidates[0]
            return candidates[0]
    except Exception as e:
        logger.warning(f"Groq model discovery failed: {e}. Defaulting to llama-3.3-70b-versatile.")

    return "llama-3.3-70b-versatile"


def format_schema_block(pruned_schema: Dict[str, Any]) -> str:
    """Formats pruned schema and dynamic foreign key bridges into readable prompt text."""
    if not isinstance(pruned_schema, dict):
        return ""

    lines = []
    for table_name, columns in pruned_schema.items():
        bracketed_table = ".".join(f"[{p}]" for p in table_name.split("."))
        lines.append(f"TABLE: {bracketed_table}")
        if isinstance(columns, list):
            for c in columns:
                col_name = c.get('column_name') or c.get('name') or 'unknown'
                col_type = c.get('data_type') or c.get('type') or ''
                lines.append(f"  - [{col_name}] ({col_type})")
        lines.append("")

    active_tables = list(pruned_schema.keys())
    dynamic_relations = get_dynamic_relationships(active_tables)
    if not dynamic_relations:
        dynamic_relations = get_dynamic_relationships(None)

    if dynamic_relations:
        lines.append("KNOWN RELATIONSHIPS:")
        for r in dynamic_relations:
            lines.append(f"  - {r}")

    return "\n".join(lines)


def format_few_shot_block(few_shots: List[Dict[str, str]]) -> str:
    """Formats few-shot question and T-SQL pairs."""
    blocks = []
    for i, ex in enumerate(few_shots, 1):
        q = ex.get("question", "")
        s = ex.get("sql", "")
        s = re.sub(r"`order`", "[order]", s)
        blocks.append(f"Example {i}:\nQ: {q}\nT-SQL: {s}")
    return "\n\n".join(blocks)


def build_prompt(question: str, pruned_schema: Dict[str, Any], few_shots: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """Constructs the system and user messages for the Groq LLM."""
    if few_shots is None:
        few_shots = DEFAULT_FEW_SHOTS

    schema_block = format_schema_block(pruned_schema)
    few_shot_block = format_few_shot_block(few_shots)

    user_content = f"""SCHEMA:
{schema_block}

FEW-SHOT EXAMPLES:
{few_shot_block}

QUESTION:
{question}

Respond with the JSON object for Microsoft SQL Server:"""

    return [
        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        {"role": "user", "content": user_content},
    ]


def call_groq(messages: List[Dict[str, str]], model_override: Optional[str] = None) -> str:
    """Invokes the Groq API with JSON object response format."""
    api_key = get_groq_api_key()
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set. Please set it in .env or settings.")

    client = Groq(api_key=api_key)
    configured_model = get_groq_model()

    if model_override:
        resolved_model = model_override
    elif configured_model and configured_model != "auto":
        resolved_model = configured_model
    else:
        resolved_model = discover_groq_model(client)

    logger.info(f"Calling Groq with model: {resolved_model}")
    resp = client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        temperature=0.1,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def parse_structured_output(raw_text: str) -> Dict[str, Any]:
    """
    Parses LLM output into {"generated_sql": ..., "explanation": ...}.
    Handles strict JSON, markdown code fence stripping, and fallback regex extraction.
    """
    text = raw_text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()

    # Attempt 1: Strict JSON
    json_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if "generated_sql" in data:
                sql_val = data.get("generated_sql")
                if sql_val:
                    sql_str = re.sub(r'\[([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\]', r'[\1].[\2]', str(sql_val).strip())
                else:
                    sql_str = None
                return {
                    "generated_sql": sql_str,
                    "explanation": str(data.get("explanation", "")).strip(),
                }
        except json.JSONDecodeError:
            pass

    # Attempt 2: Regex fallback for SELECT query
    sql_match = re.search(r"(SELECT\b.*?;)", text, flags=re.IGNORECASE | re.DOTALL)
    if sql_match:
        generated_sql = re.sub(r'\[([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\]', r'[\1].[\2]', sql_match.group(1).strip())
        remainder = (text[:sql_match.start()] + text[sql_match.end():])
        remainder = re.sub(r'"?generated_sql"?\s*:?', "", remainder, flags=re.IGNORECASE)
        remainder = re.sub(r'"?explanation"?\s*:?', "", remainder, flags=re.IGNORECASE)
        remainder = remainder.strip(" \n\t{}\",")
        explanation = remainder if remainder else "Query generated based on banking schema."
        return {"generated_sql": generated_sql, "explanation": explanation}

    # Attempt 3: Non-SQL conversational explanation
    return {
        "generated_sql": None,
        "explanation": text.strip(" \n\t{}\",") or "No query could be generated for this request."
    }

