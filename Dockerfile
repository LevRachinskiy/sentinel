FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements-lock.txt ./
COPY backend ./backend
RUN pip install --no-cache-dir -r requirements-lock.txt && pip install --no-cache-dir . --no-deps && useradd --uid 10001 --create-home sentinel && mkdir /data && chown sentinel /data
USER sentinel
ENV DATABASE_URL=sqlite:////data/sentinel.db
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "sentinel.api:application", "--factory", "--host", "0.0.0.0", "--port", "8000"]
