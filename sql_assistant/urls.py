"""
URL routes for sql_assistant.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('api/query/', views.api_execute_query, name='api_execute_query'),
    path('api/history/', views.api_query_history, name='api_query_history'),
    path('api/schema/', views.api_schema_explorer, name='api_schema_explorer'),
    path('api/status/', views.api_status, name='api_status'),
]

