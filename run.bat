@echo off
title ApexBank Text-to-SQL Web App
echo ========================================================
echo Starting ApexBank Text-to-SQL Server...
echo Target Database: Microsoft SQL Server (.\SQLEXPRESS / financial)
echo ========================================================
echo Opening browser at http://127.0.0.1:8000 ...
python manage.py runserver 127.0.0.1:8000
pause

