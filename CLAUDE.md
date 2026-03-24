# CLAUDE.md

## Project Overview

MiroShark is a Universal Swarm Intelligence Engine that simulates public reaction to documents on social media. It takes documents (press releases, policy drafts, financial reports), generates hundreds of AI agents with unique personas, and simulates their interactions on social platforms using the CAMEL-AI OASIS framework. Results include analysis reports and direct agent interaction.

## Architecture

**Full-stack application:**
- **Backend:** Python 3.11+ / Flask 3.0+ (REST API on port 5001)
- **Frontend:** Vue 3 / Vite (dev server on port 3000)
- **Database:** Neo4j 5.15+ (graph database for knowledge graphs)
- **LLM:** Flexible — OpenAI-compatible APIs, Ollama, OpenRouter, Claude Code CLI
- **Simulation:** CAMEL-AI OASIS framework (multi-agent, multi-process)

**Key architecture patterns:**
- Flask Blueprints for modular API routes (`/api/graph`, `/api/simulation`, `/api/report`)
- Service layer pattern: API routes → Services → Storage
- Unified LLM interface (`app/utils/llm_client.py`) routing to multiple providers
- Smart Model tier for intelligence-sensitive workflows (reports, ontology) with fallback to default model
- Neo4j singleton via `app.extensions` for dependency injection
- Multi-process simulation with IPC (`simulation_ipc.py`)

## Directory Structure

```
MiroShark/
├── backend/
│   ├── run.py                          # Entry point (Flask on port 5001)
│   ├── pyproject.toml                  # Python deps (uv package manager)
│   ├── requirements.txt                # Flattened deps
│   ├── uv.lock                         # Locked dependency versions
│   └── app/
│       ├── __init__.py                 # Flask app factory
│       ├── config.py                   # Config from .env
│       ├── api/
│       │   ├── graph.py                # Graph building endpoints
│       │   ├── simulation.py           # Simulation management endpoints
│       │   └── report.py               # Report generation endpoints
│       ├── services/                   # Business logic (~12 files)
│       │   ├── graph_builder.py
│       │   ├── ontology_generator.py
│       │   ├── simulation_runner.py    # Runs CAMEL-AI OASIS
│       │   ├── simulation_manager.py
│       │   ├── report_agent.py         # Multi-turn report generation
│       │   └── ...
│       ├── storage/                    # Data persistence
│       │   ├── neo4j_storage.py        # Neo4j driver and queries
│       │   ├── neo4j_schema.py         # Database schema
│       │   ├── embedding_service.py    # Embeddings (Ollama or OpenAI)
│       │   └── ...
│       ├── models/                     # Data models
│       │   ├── project.py              # Project management
│       │   └── task.py                 # Async task tracking
│       └── utils/
│           ├── llm_client.py           # Unified LLM interface
│           ├── claude_code_client.py   # Claude Code CLI integration
│           ├── file_parser.py          # PDF/TXT/MD parsing
│           ├── logger.py
│           └── retry.py
├── frontend/
│   ├── package.json                    # Vue 3, Vite, D3.js, Axios
│   ├── vite.config.js                  # Port 3000, API proxy to 5001
│   └── src/
│       ├── main.js                     # Vue app entry
│       ├── App.vue                     # Root component
│       ├── api/                        # Axios API client modules
│       ├── router/index.js             # 6 routes
│       ├── views/                      # Page components
│       ├── components/                 # Steps 1-5, GraphPanel, etc.
│       ├── store/                      # State management
│       └── assets/
├── .env.example                        # Config template
├── Dockerfile                          # Multi-stage (Python + Node)
├── docker-compose.yml                  # Neo4j + Ollama + app
├── package.json                        # Root: runs both services
└── .github/workflows/docker-image.yml  # CI: Docker build on tags
```

## Development Setup

### Quick Start

```bash
# 1. Copy environment config
cp .env.example .env
# Edit .env with your LLM provider settings

# 2. Install all dependencies
npm run setup:all

# 3. Start both services (backend + frontend concurrently)
npm run dev
```

### Individual Commands

```bash
# Backend only
npm run setup:backend    # cd backend && uv sync
npm run backend          # cd backend && uv run python run.py

# Frontend only
npm run setup            # npm install && cd frontend && npm install
npm run frontend         # cd frontend && npm run dev
npm run build            # cd frontend && npm run build
```

### Docker

```bash
docker-compose up        # Starts Neo4j, Ollama, and MiroShark
```

### Dependencies

- **Backend:** Managed with `uv` (Python package manager). Lock file: `backend/uv.lock`
- **Frontend:** Managed with `npm`. Lock file: `frontend/package-lock.json`
- **Root:** Uses `concurrently` to run both services

## Environment Configuration

Key variables in `.env` (see `.env.example` for full list):

| Variable | Purpose | Default |
|----------|---------|---------|
| `LLM_PROVIDER` | "openai" or "claude-code" | "openai" |
| `LLM_BASE_URL` | LLM endpoint | `http://localhost:11434/v1` |
| `LLM_MODEL_NAME` | Model to use | — |
| `LLM_API_KEY` | API key | — |
| `SMART_PROVIDER` | Optional stronger model provider | falls back to default |
| `SMART_MODEL_NAME` | Stronger model for reports/ontology | falls back to default |
| `NEO4J_URI` | Neo4j connection | `bolt://localhost:7687` |
| `NEO4J_PASSWORD` | Neo4j password | `miroshark` |
| `EMBEDDING_PROVIDER` | "ollama" or "openai" | — |
| `EMBEDDING_MODEL` | Embedding model name | — |
| `OPENAI_API_KEY` | Required for OASIS simulation | — |

## Testing

```bash
cd backend && uv run pytest
```

Testing infrastructure is minimal. Optional dev deps include `pytest>=8.0.0` and `pytest-asyncio>=0.23.0` in `pyproject.toml`.

## Code Conventions

### Backend (Python)
- **Naming:** snake_case for functions/variables, PascalCase for classes
- **Style:** PEP 8 (no enforced linter configured)
- **Data validation:** Pydantic 2.0+
- **Docstrings:** Present on major functions and classes
- **API routes:** RESTful GET/POST with `/api/` prefix, Flask Blueprints
- **Error handling:** Task-based async tracking (`TaskStatus` states)

### Frontend (JavaScript/Vue)
- **Components:** PascalCase filenames (e.g., `Step1GraphBuild.vue`)
- **Functions:** camelCase
- **Framework:** Vue 3 Composition API
- **HTTP:** Axios with 300-second timeout, interceptors for error handling
- **State:** Props-based communication + simple store for uploads

### General
- No linter or formatter is enforced — follow existing style in each file
- No pre-commit hooks configured
- Commit messages follow conventional format: `type: description` (e.g., `feat:`, `fix:`, `docs:`, `refactor:`)

## CI/CD

GitHub Actions workflow (`.github/workflows/docker-image.yml`):
- **Triggers:** Tag pushes + manual dispatch
- **Action:** Build multi-platform Docker image → push to `ghcr.io/aaronjmars/miroshark`
- No automated tests in CI

## Key Files for Common Tasks

| Task | Files |
|------|-------|
| Add a new API endpoint | `backend/app/api/` (add to existing blueprint or create new one, register in `__init__.py`) |
| Modify LLM behavior | `backend/app/utils/llm_client.py`, `backend/app/config.py` |
| Change simulation logic | `backend/app/services/simulation_runner.py`, `simulation_manager.py` |
| Modify graph/knowledge base | `backend/app/services/graph_builder.py`, `backend/app/storage/neo4j_storage.py` |
| Update report generation | `backend/app/services/report_agent.py`, `backend/app/api/report.py` |
| Add a frontend route | `frontend/src/router/index.js`, add view in `frontend/src/views/` |
| Add an API client call | `frontend/src/api/` |
| Change environment config | `.env.example`, `backend/app/config.py` |

## Important Notes

- Backend runs on port **5001**, frontend on port **3000** with Vite proxy to backend
- The OASIS simulation runs in a **separate process** — changes to simulation logic must account for IPC
- `simulation.py` API file is very large (~95KB) — take care when editing
- `report_agent.py` is also large (~102KB) — contains multi-turn ReACT loop for report generation
- Neo4j must be running for the application to function (graph database is central to the pipeline)
- Uploaded documents are stored locally; check `MAX_CONTENT_LENGTH` (50MB default) and `ALLOWED_EXTENSIONS` (pdf, md, txt, markdown)
