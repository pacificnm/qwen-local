# QWEN.md - Project Context for AI-Assisted Development

## Project Overview

**Name:** chat.folding-os.com  
**Type:** Self-hosted AI codebase assistant  
**Purpose:** Provide a ChatGPT/Claude-like interface for software engineers to chat with their codebases, generate code, and push changes directly to GitHub.

## Tech Stack

- **Backend:** FastAPI + Qwen-Agent (Python 3.12)
- **Frontend:** React 19 + TypeScript + Vite + Zustand + Monaco Editor (locked)
- **Database:** PostgreSQL 17+ with pgvector (runs on host, not in Docker)
- **Inference:** Pre-existing Ollama (`qwen3.5:4b` + `qwen3.8:27b-longctx` + `nomic-embed-text`) on a single Tesla V100, out of scope to configure
- **Search:** Self-hosted SearXNG container (JSON API) for Qwen-Agent web search
- **Deployment:** Docker containers for frontend, backend, and SearXNG; public access via the pre-existing Cloudflare Tunnel (no host ports published)
- **Code Parsing:** Tree-sitter for language-aware chunking (JS, TS, Python, SQL)

## Architecture

- Hybrid deployment: Ollama and PostgreSQL run natively on the host for maximum performance
- Frontend and Backend run in Docker containers
- Backend communicates with host services via `host.docker.internal`
- Ephemeral Docker containers for sandboxed code execution

## Key Features

1. **Single-User Auth:** one shared username/password account (no GitHub OAuth); GitHub access via a single static PAT (env only) for clone/branch/PR
2. **Dual Models:** `qwen3.5:4b` (fast) and `qwen3.8:27b-longctx` (default) with an in-UI model picker
3. **Language-Aware RAG:** Tree-sitter parses code into whole functions/classes before embedding; pgvector top-8 retrieval
4. **Bi-Directional Sync:** Edit AI-generated code in Monaco Editor and push to GitHub as branches/PRs
5. **Agentic Tools:** Code Interpreter (ephemeral sandbox, no network, 120 s) and web search via self-hosted SearXNG
6. **Real-time Streaming:** SSE streams agent thinking, tool calls, and responses to UI; full stop button cancels generation and active tools

## Project Structure
- **See Project structure** `docs/PROJECT_STRUCTURE.md`


## Development Guidelines

- **Read the docs first:** Check `docs/MASTER_SPEC.md` (decision log §1.1 has all locked choices) for complete requirements and phased implementation plan
- **No code examples in README:** Keep documentation clean; put examples in docs or inline comments
- **Host services:** Ollama (port 11434) and PostgreSQL (port 5432) run on the host machine, not in containers; Ollama is pre-existing — never pull/tune models from the app
- **Docker networking:** Use `host.docker.internal` to reach host services from containers; frontend/backend attach to the tunnel's external network (`cloudflared_default`) for public access
- **Access:** LAN devices use the published frontend port directly — `http://192.168.88.10:3000` (host port 3000 is free since the open-webui stack was decommissioned 2026-08-23; 8000/8080 are taken by other host apps). The Cloudflare Tunnel remains for off-site access (`chat.folding-os.com`). Do not remove the published 3000 or the tunnel without explicit request
- **Security:** `GITHUB_PAT` and `SECRET_KEY` are env-only (never in DB or logs); single account uses argon2id hashes; never commit `.env` files

## How to Assist

When helping with this project:

1. **Context:** Always reference the Master Specification (`docs/MASTER_SPEC.md`) for detailed requirements
2. **Architecture:** Follow the hybrid deployment model (host services + Docker containers)
3. **Tech choices:** Use FastAPI for backend, Qwen-Agent for orchestration, Tree-sitter for parsing, pgvector for embeddings
4. **Testing:** Provide instructions for testing without the UI (e.g., curl commands, Python scripts)
5. **Security:** Never expose secrets; use environment variables and encryption for sensitive data

## Quick References

- **Full Spec:** `docs/MASTER_SPEC.md`
- **Architecture:** `docs/ARCHITECTURE.md`
- **API Docs:** `docs/API.md`
- **Backend Entry:** `backend/app/main.py`
- **Frontend Entry:** `frontend/src/main.tsx` (or equivalent)
