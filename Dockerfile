FROM python:3.12-slim

WORKDIR /app

# Install dependencies. requirements-optional.txt* (the trailing * is a glob, not
# an optional-file flag) tolerates that file being absent — the two-deletion
# opt-out (see backend/requirements-optional.txt) must not break the build. A bare
# `COPY backend/requirements-optional.txt .` would hard-fail the build on a missing
# path; a `requirements*.txt` glob would also catch requirements-dev.txt and bust
# this layer's cache on every test-only dependency change.
COPY backend/requirements.txt backend/requirements-optional.txt* ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ -f requirements-optional.txt ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

# Copy backend code
COPY backend/ .

# Copy frontend for static serving
COPY frontend/ /frontend

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
