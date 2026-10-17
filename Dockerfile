# Use the official lightweight Python image (native to IBM s390x)
FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Install gcc and standard libraries required for subprocess sandboxing limits
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project directory (including the backend folder)
COPY . .

# Expose port 8000 for the frontend to communicate with the API
EXPOSE 8000

# Launch the FastAPI web server pointing to the backend directory
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]