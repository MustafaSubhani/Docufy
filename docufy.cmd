@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m spark_agent serve
