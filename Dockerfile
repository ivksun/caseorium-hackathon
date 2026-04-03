FROM node:22-slim AS base

# Install Python
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv git && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN python3 -m venv /app/.venv && \
    /app/.venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Env vars (override at runtime)
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python3", "server.py"]
