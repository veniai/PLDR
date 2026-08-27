FROM python:3.12-slim
WORKDIR /app
COPY services/intel-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
ENV PYTHONPATH=/app/services/intel-api
ENV PLDR_DATABASE_URL=sqlite:////app/data/runtime/pldr-p0.db
EXPOSE 8765
CMD ["uvicorn", "pldr_api.main:app", "--host", "0.0.0.0", "--port", "8765"]
