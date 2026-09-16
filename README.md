# ApexBank Text-to-SQL Intelligence Platform

A clean, production-grade Django web application powered by **Groq LLMs**, **SQLAlchemy dynamic foreign-key linking**, and **sqlglot security guardrails**, generating and executing Microsoft SQL Server (T-SQL) queries against a local database instance (`.\SQLEXPRESS / financial`).

---

## Key Features

1. **Pure Microsoft SQL Server (T-SQL) Generation**:
   - Generates proper T-SQL syntax with `SELECT TOP 1000 ...`, bracketed identifiers (`[order]`, `[loan]`), and exact join clauses.
   - Dynamic foreign key & bridge relationship discovery (e.g., auto-discovers `disp` as the link between `client` and `account`).
2. **Conversational & Safety Guardrails**:
   - Instant zero-token greeting interceptor for casual greetings (`"hello"`, `"help"`).
   - Chit-chat & off-topic question detector in system instructions that returns friendly guidance without generating broken SQL.
   - Read-only AST validation via `sqlglot` that blocks destructive commands (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`).
3. **Live SQL Server Connectivity**:
   - Primary: Microsoft SQL Server (`.\SQLEXPRESS / financial`) via Windows Trusted Authentication.
   - Fallback: Automatic fallback to local `financial.sqlite` if SQL Server is stopped.
4. **Sleek FinTech Dashboard**:
   - Styled with Tailwind CSS (dark mode slate/navy aesthetic).
   - Natural language query input with suggestions & keyboard shortcuts (`Ctrl+Enter`).
   - Monospace SQL viewer with syntax highlighting and copy-to-clipboard.
   - Live interactive data table with row search and CSV export.
   - Schema Explorer modal inspecting tables, columns, and foreign keys.
   - Audit history tracking every query, latency (ms), status, and staff role.

---

## Quick Start

### 1. Launch the Server
Double click `run.bat` or run:
```bash
python manage.py runserver 127.0.0.1:8000
```

### 2. Access the Application
- **Bank Staff Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Django Admin Audit Portal**: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
  - Username: `admin`
  - Password: `admin123`

---

## Project Structure

```
bank_text2sql_django/
├── .env                       # Groq API key and SQL Server connection settings
├── run.bat                    # One-click Windows runner
├── manage.py                  # Django CLI
├── bank_portal/               # Django project configuration
│   ├── settings.py            # Apps, database, templates, and static setup
│   ├── urls.py                # Top-level URL routing
│   └── wsgi.py
├── sql_assistant/             # Main Text-to-SQL application
│   ├── models.py              # QueryHistory (audit log) and SavedQuery
│   ├── views.py               # Dashboard view and JSON REST APIs
│   ├── urls.py                # API & view routes
│   ├── admin.py               # Django Admin audit configuration
│   └── engine/                # Core Text-to-SQL modules
│       ├── config.py          # Environment settings accessor
│       ├── db_manager.py      # SQLAlchemy MSSQL/SQLite connection manager
│       ├── dynamic_linker.py  # Foreign key graph & bridge relationship linker
│       ├── retrieval.py       # Domain classifier, table matcher & column pruner
│       ├── groq_service.py    # Groq API client, T-SQL prompt & JSON parser
│       ├── security_guardrail.py # Read-only T-SQL AST validator & safe executor
│       └── pipeline.py        # Master pipeline entry point (text_to_sql)
├── templates/
│   ├── base.html              # Base layout with Tailwind, navigation & PrismJS
│   └── dashboard.html         # Staff dashboard, SQL viewer, data table & modal
└── static/                    # Custom static assets
```

