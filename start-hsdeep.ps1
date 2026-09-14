cd I:\AI-LAB\hsdeep-core-ai\backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
uvicorn app.main:app --reload