# Sous Recipe Manager Dockerfile

# Use Python 3.11 slim image as base (bumped from 3.9: ingredient-parser-nlp
# requires Python >=3.11 - still within the "Python 3.9+" range SPEC.md
# declares as the target)
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements file and install (litellm for the easter-egg feature,
# ingredient-parser-nlp for ingredient parsing - everything else is
# standard library, see requirements.txt for details)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ingredient-parser-nlp needs one NLTK data package at runtime; fetch it
# at build time so it's baked into the image instead of being downloaded
# on every container start. Downloaded explicitly to a world-readable,
# NLTK-standard search path (not the default ~/nltk_data, which at this
# point in the build would resolve under /root - unreadable by the
# unprivileged appuser this image switches to below) and pointed at via
# NLTK_DATA so it's found regardless of which user the process runs as.
ENV NLTK_DATA=/usr/local/share/nltk_data
RUN python -c "import nltk; nltk.download('averaged_perceptron_tagger_eng', download_dir='/usr/local/share/nltk_data')" \
    && chmod -R a+rX /usr/local/share/nltk_data

# Copy application code
COPY . .

# Expose port 8000
EXPOSE 8000

# Create non-root user for security
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

# Run the application
CMD ["python", "server.py"]