"""
Database connection manager and live schema inspector for Microsoft SQL Server / SQLite fallback.
"""
import os
import urllib.parse
import logging
from typing import Dict, Any, List, Tuple
from sqlalchemy import create_engine, inspect, text as sa_text
from sqlalchemy.engine import Engine

from .config import get_mssql_config, get_sqlite_fallback_path

logger = logging.getLogger("sql_assistant.db_manager")

_ENGINE: Engine | None = None
_ACTIVE_DIALECT: str = "tsql"
_ACTIVE_DB_NAME: str = ""
_CACHED_CATALOG: Dict[str, Any] | None = None


def get_database_engine() -> Tuple[Engine, str, str]:
    """
    Returns (engine, dialect, db_description).
    Attempts to connect to Microsoft SQL Server (.\\SQLEXPRESS) first.
    Falls back gracefully to local financial.sqlite if MSSQL is not available.
    """
    global _ENGINE, _ACTIVE_DIALECT, _ACTIVE_DB_NAME

    if _ENGINE is not None:
        return _ENGINE, _ACTIVE_DIALECT, _ACTIVE_DB_NAME

    mssql_cfg = get_mssql_config()
    server = mssql_cfg["server"]
    db_name = mssql_cfg["database"]
    driver = mssql_cfg["driver"]
    trusted = mssql_cfg["trusted_connection"]

    # 1. Try Microsoft SQL Server (T-SQL) via pyodbc
    try:
        raw_conn_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={db_name};Trusted_Connection={trusted};"
        params = urllib.parse.quote_plus(raw_conn_str)
        mssql_url = f"mssql+pyodbc:///?odbc_connect={params}" 

        # pyodbc is used for connecting to Microsoft SQL Server. The connection string is constructed with the provided server, database, driver, and trusted connection settings. The connection string is then URL-encoded and used to create a SQLAlchemy engine.

        engine = create_engine(mssql_url, pool_pre_ping=True)
        # Test connection
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        
        _ENGINE = engine
        _ACTIVE_DIALECT = "tsql"
        _ACTIVE_DB_NAME = f"MS SQL Server ({server} / {db_name})"
        logger.info(f"Connected to Microsoft SQL Server: {_ACTIVE_DB_NAME}")
        return _ENGINE, _ACTIVE_DIALECT, _ACTIVE_DB_NAME

    except Exception as mssql_err:
        logger.warning(f"Could not connect to MSSQL ({server}): {mssql_err}. Falling back to SQLite.")

    # 2. Fallback to financial.sqlite
    sqlite_path = get_sqlite_fallback_path()
    if os.path.exists(sqlite_path):
        engine = create_engine(f"sqlite:///{sqlite_path}")
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        _ENGINE = engine
        _ACTIVE_DIALECT = "sqlite"
        _ACTIVE_DB_NAME = f"SQLite Fallback ({os.path.basename(sqlite_path)})"
        logger.info(f"Connected to SQLite Fallback: {_ACTIVE_DB_NAME}")
        return _ENGINE, _ACTIVE_DIALECT, _ACTIVE_DB_NAME

    raise RuntimeError(
        f"Unable to connect to Microsoft SQL Server ({server}) and fallback database not found at {sqlite_path}."
    )


def reset_engine():
    """Forces engine reconnection on next request (e.g. after config change)."""
    global _ENGINE, _ACTIVE_DIALECT, _ACTIVE_DB_NAME, _CACHED_CATALOG
    _ENGINE = None
    _ACTIVE_DIALECT = "tsql"
    _ACTIVE_DB_NAME = ""
    _CACHED_CATALOG = None


def extract_live_metadata(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Dynamically extracts tables, columns, data types, primary keys,
    foreign keys, and sample rows from the connected database.
    """
    global _CACHED_CATALOG

    if _CACHED_CATALOG is not None and not force_refresh:
        return _CACHED_CATALOG

    engine, dialect, _ = get_database_engine()
    inspector = inspect(engine)

    catalog = {}
    IGNORED_TABLES = {"sysdiagrams", "sqlite_sequence", "django_migrations", "django_content_type"}
    SYSTEM_SCHEMAS = {
        "sys", "information_schema", "guest", "db_accessadmin", "db_backupoperator",
        "db_datareader", "db_datawriter", "db_ddladmin", "db_denydatareader",
        "db_denydatawriter", "db_owner", "db_securityadmin"
    }

    if dialect == "tsql":
        raw_schemas = inspector.get_schema_names()
        user_schemas = [s for s in raw_schemas if s.lower() not in SYSTEM_SCHEMAS]
        all_tables = []
        for s in user_schemas:
            for t in inspector.get_table_names(schema=s):
                if t.lower() not in IGNORED_TABLES:
                    all_tables.append((s, t))
    else:
        all_tables = [(None, t) for t in inspector.get_table_names() if t.lower() not in IGNORED_TABLES]

    for schema, tbl in all_tables:
        display_name = f"{schema}.{tbl}" if schema and schema.lower() != "dbo" else tbl
        qualified_select = f"[{schema}].[{tbl}]" if schema else f"[{tbl}]"

        try:
            columns = inspector.get_columns(tbl, schema=schema)
        except Exception:
            columns = []

        try:
            pk_info = inspector.get_pk_constraint(tbl, schema=schema) or {}
            pks = pk_info.get("constrained_columns", [])
        except Exception:
            pks = []

        try:
            fks = inspector.get_foreign_keys(tbl, schema=schema) or []
        except Exception:
            fks = []

        fk_lookup = {}
        for fk in fks:
            referred_table = fk.get("referred_table")
            referred_schema = fk.get("referred_schema")
            ref_display = f"{referred_schema}.{referred_table}" if referred_schema and referred_schema.lower() != "dbo" else referred_table
            constrained = fk.get("constrained_columns", [])
            referred = fk.get("referred_columns", [])
            for loc_col, rem_col in zip(constrained, referred):
                fk_lookup[loc_col] = f"{ref_display}.{rem_col}"

        # Fetch up to 2 sample rows
        sample_rows = []
        try:
            with engine.connect() as conn:
                if dialect == "tsql":
                    res = conn.execute(sa_text(f"SELECT TOP 2 * FROM {qualified_select}"))
                else:
                    res = conn.execute(sa_text(f'SELECT * FROM "{tbl}" LIMIT 2'))
                for r in res:
                    row_dict = {}
                    for k, v in dict(r._mapping).items():
                        if isinstance(v, (bytes, bytearray, memoryview)):
                            row_dict[k] = f"<binary {len(v)} bytes>"
                        elif hasattr(v, "isoformat"):
                            row_dict[k] = v.isoformat()
                        elif isinstance(v, (int, float, str, bool)) or v is None:
                            row_dict[k] = v
                        else:
                            row_dict[k] = str(v)
                    sample_rows.append(row_dict)
        except Exception:
            pass

        column_meta = []
        for col in columns:
            cname = col["name"]
            column_meta.append({
                "column_name": cname,
                "data_type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "is_primary_key": cname in pks,
                "is_foreign_key": cname in fk_lookup,
                "references": fk_lookup.get(cname),
            })

        catalog[display_name] = {
            "table_name": display_name,
            "schema": schema,
            "primary_key": pks,
            "foreign_keys": fks,
            "columns": column_meta,
            "sample_rows": sample_rows,
        }

    _CACHED_CATALOG = catalog
    logger.info(f"Extracted metadata for {len(catalog)} tables: {list(catalog.keys())[:10]}...")
    return catalog

