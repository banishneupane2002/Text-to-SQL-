"""
Views and REST APIs for Bank Text-to-SQL Assistant.
"""
import json
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

# Create your views here.
from .engine import text_to_sql, get_pipeline_engine, get_schema_summary
from .models import QueryHistory, SavedQuery

logger = logging.getLogger("sql_assistant.views")

# Curated sample queries for intelligence dashboard
CURATED_SUGGESTIONS = [
    {
        "domain": "Sales & Revenue",
        "question": "Which top 5 sales representatives generated the highest total sales revenue, including their full name and total amount sold?",
        "badge": "Top Sales Reps"
    },
    {
        "domain": "Human Resources",
        "question": "Show the average hourly pay rate and total number of employees for each department, ordered by highest average rate.",
        "badge": "Department Pay"
    },
    {
        "domain": "Sales Analytics",
        "question": "Show the total sales revenue and order count for each sales territory in the year 2013, ordered by total revenue descending.",
        "badge": "Territory 2013"
    },
    {
        "domain": "Production & Inventory",
        "question": "List the product subcategories that have a total inventory quantity of less than 1,000 units across all storage locations, along with their total stock.",
        "badge": "Low Stock Alert"
    },
    {
        "domain": "Customer Intelligence",
        "question": "Show the top 5 customers who have spent the most money, including their full name, email address, and total amount spent.",
        "badge": "VIP Customers"
    },
    {
        "domain": "Purchasing & Procurement",
        "question": "Which 5 vendors have the highest total purchase order amounts, showing the vendor name and total order value?",
        "badge": "Top Vendors"
    },
]


def dashboard_view(request):
    """Renders the main bank staff intelligence dashboard."""
    _, dialect, db_name = get_pipeline_engine()
    recent_queries = QueryHistory.objects.all()[:8]
    saved_queries = SavedQuery.objects.all()[:6]

    context = {
        "active_database": db_name,
        "dialect": dialect.upper(),
        "recent_queries": recent_queries,
        "saved_queries": saved_queries,
        "suggestions": CURATED_SUGGESTIONS,
    }
    return render(request, "dashboard.html", context)


@csrf_exempt
@require_POST
def api_execute_query(request):
    """
    POST API to process natural language questions.
    Payload: {"question": "...", "role": "Analyst"}
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
        question = data.get("question", "").strip()
        role = data.get("role", "Analyst")

        if not question:
            return JsonResponse({"success": False, "error": "Question cannot be empty."}, status=400)

        # Run pipeline
        res = text_to_sql(question, validate=True)

        # Determine status for audit logging
        validation = res.get("validation", {})
        error_msg = validation.get("error")

        if res.get("is_chit_chat") and not res.get("generated_sql"):
            status = 'CHIT_CHAT'
        elif validation.get("success"):
            status = 'SUCCESS'
        elif error_msg and ("policy" in error_msg.lower() or "forbidden" in error_msg.lower()):
            status = 'BLOCKED'
        else:
            status = 'ERROR'

        # Persist audit record in Django DB
        QueryHistory.objects.create(
            question=question,
            generated_sql=res.get("generated_sql"),
            explanation=res.get("explanation", ""),
            detected_domain=res.get("detected_domain", "General"),
            status=status,
            execution_time_ms=res.get("elapsed_time_ms", 0.0),
            rows_count=validation.get("rows_count", 0),
            error_message=error_msg,
            staff_role=role,
            database_name=res.get("database_target", "")
        )

        return JsonResponse({
            "success": validation.get("success", False),
            "status": status,
            "question": question,
            "is_chit_chat": res.get("is_chit_chat", False),
            "generated_sql": res.get("generated_sql"),
            "explanation": res.get("explanation", ""),
            "detected_domain": res.get("detected_domain"),
            "candidate_tables": res.get("candidate_tables", []),
            "database_target": res.get("database_target"),
            "dialect": res.get("dialect"),
            "elapsed_time_ms": res.get("elapsed_time_ms"),
            "model_used": res.get("model_used", "llama-3.1-8b-instant"),
            "auto_healed": res.get("auto_healed", False),
            "columns": validation.get("columns", []),
            "rows": validation.get("rows", []),
            "rows_count": validation.get("rows_count", 0),
            "error": error_msg
        })

    except Exception as e:
        logger.exception("Error handling natural language query")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_GET
def api_query_history(request):
    """Returns recent query audit logs."""
    history = QueryHistory.objects.all()[:25]
    records = []
    for h in history:
        records.append({
            "id": h.id,
            "question": h.question,
            "generated_sql": h.generated_sql,
            "explanation": h.explanation,
            "status": h.status,
            "execution_time_ms": h.execution_time_ms,
            "rows_count": h.rows_count,
            "created_at": h.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "error_message": h.error_message
        })
    return JsonResponse({"history": records})


@require_GET
def api_schema_explorer(request):
    """Returns live database tables, columns, and foreign key graph."""
    try:
        schema = get_schema_summary()
        return JsonResponse(schema, json_dumps_params={'default': str})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def api_status(request):
    """Returns database connection status."""
    try:
        _, dialect, db_name = get_pipeline_engine()
        return JsonResponse({
            "status": "online",
            "database": db_name,
            "dialect": dialect.upper()
        })
    except Exception as e:
        return JsonResponse({
            "status": "offline",
            "error": str(e)
        }, status=500)
