# Multi-stage so local development can have test dependencies without shipping them
# to Cloud Run. `prod` is deliberately the LAST stage: an untargeted `docker build`
# — which is what cloudbuild.yaml runs — resolves to the final stage, so production
# stays lean by default and nobody has to remember a flag.

FROM python:3.12-slim AS base

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


# Development image — adds pytest and anything else in requirements-dev.txt.
# Selected by docker-compose.yml via `target: dev`, so `docker-compose up --build`
# gives you a container you can run the test suite in without a manual pip install.
FROM base AS dev
RUN pip install --no-cache-dir -r requirements-dev.txt


# Production image. An alias of `base` with no test dependencies, kept last so it
# is what an untargeted build produces.
FROM base AS prod
