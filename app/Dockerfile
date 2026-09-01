FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
# Flask's dev server. A production image would use gunicorn; kept simple here
# because the app is not what this project is demonstrating.
CMD ["python", "app.py"]
