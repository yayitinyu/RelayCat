FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose Web Admin Port
EXPOSE 8080

# Environment variables (Defaults)
ENV RELAYCAT_DATA_DIR=/data
ENV RELAYCAT_DB_URL=sqlite+aiosqlite:////data/relaycat.db

# Output directory for data
VOLUME /data

# Command to run the application (Main entry point)
CMD ["python", "-m", "app.main"]
