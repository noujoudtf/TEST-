FROM python:3.11-slim
WORKDIR /app
# copy backend requirements
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
# copy backend code
COPY backend /app
# copy frontend into the image so StaticFiles can serve it
COPY frontend /app/frontend
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
