"""
Django Admin configuration for Bank Text-to-SQL Assistant.
Enables query auditing and monitoring of all natural language queries.
"""
from django.contrib import admin
from .models import QueryHistory, SavedQuery

# Register your models here.

@admin.register(QueryHistory)
class QueryHistoryAdmin(admin.ModelAdmin):
    list_display = ('question_short', 'status', 'detected_domain', 'execution_time_ms', 'rows_count', 'staff_role', 'database_name', 'created_at')
    list_filter = ('status', 'detected_domain', 'staff_role', 'created_at')
    search_fields = ('question', 'generated_sql', 'explanation', 'error_message')
    readonly_fields = ('created_at', 'execution_time_ms')

    def question_short(self, obj):
        return obj.question[:70] + ('...' if len(obj.question) > 70 else '')
    question_short.short_description = 'Question'


@admin.register(SavedQuery)
class SavedQueryAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'created_at')
    list_filter = ('category',)
    search_fields = ('title', 'question', 'sql')
