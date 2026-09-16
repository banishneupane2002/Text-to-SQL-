"""
Django Models for Bank Text-to-SQL Assistant.
Persists query audit history and bookmarked queries.
"""
from django.db import models

# Create your models here.

class QueryHistory(models.Model):
    STATUS_CHOICES = [
        ('SUCCESS', 'Success'),
        ('BLOCKED', 'Security Blocked'),
        ('ERROR', 'Execution Error'),
        ('CHIT_CHAT', 'Chit Chat / Info'),
    ]

    question = models.TextField(help_text="The natural language question submitted by staff.")
    generated_sql = models.TextField(blank=True, null=True, help_text="The generated Microsoft SQL Server T-SQL query.")
    explanation = models.TextField(blank=True, help_text="Plain English explanation of the query.")
    detected_domain = models.CharField(max_length=100, blank=True, help_text="Banking domain classification.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUCCESS')
    execution_time_ms = models.FloatField(default=0.0, help_text="Pipeline execution latency in milliseconds.")
    rows_count = models.IntegerField(default=0, help_text="Number of preview rows returned.")
    error_message = models.TextField(blank=True, null=True, help_text="Error message if execution or validation failed.")
    staff_role = models.CharField(max_length=50, default='Analyst', help_text="Role clearance of the user.")
    database_name = models.CharField(max_length=200, blank=True, help_text="Target database where query ran.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Query History Record"
        verbose_name_plural = "Query History Records"

    def __str__(self):
        return f"[{self.status}] {self.question[:60]} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"


class SavedQuery(models.Model):
    title = models.CharField(max_length=200, help_text="Short label or title for the query.")
    question = models.TextField(help_text="The natural language question.")
    sql = models.TextField(help_text="The verified T-SQL query.")
    category = models.CharField(max_length=100, default='General', help_text="Category or domain.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'title']
        verbose_name = "Saved Query"
        verbose_name_plural = "Saved Queries"

    def __str__(self):
        return f"{self.title} ({self.category})"
