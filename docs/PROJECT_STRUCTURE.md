chat.folding-os.com/
├── README.md                  # Project overview, setup instructions, and quick start
├── docker-compose.yml         # Orchestrates the frontend and backend containers
├── .env.example               # Template for environment variables (API keys, DB URIs)
├── .gitignore                 # Standard ignores (node_modules, .env, __pycache__, etc.)
│
├── docs/                      # Project documentation
│   ├── MASTER_SPEC.md         # The specification document we just created
│   ├── API.md                 # OpenAPI/Swagger documentation for backend endpoints
│   └── ARCHITECTURE.md        # Diagrams and notes on data flow
│
├── backend/                   # FastAPI + Qwen-Agent Application
│   ├── Dockerfile             # Container definition for the backend
│   ├── pyproject.toml         # Python dependencies (FastAPI, qwen-agent, psycopg2, etc.)
│   ├── app/
│   │   ├── main.py            # FastAPI application entry point and CORS setup
│   │   ├── core/              # Core configurations (settings.py, logging, security)
│   │   ├── db/                # Database layer
│   │   │   ├── base.py        # SQLAlchemy base and pgvector setup
│   │   │   ├── models.py      # SQLAlchemy models (User, Repo, CodeChunk, Conversation, Message)
│   │   │   └── session.py     # Database connection management
│   │   ├── api/               # API Routes
│   │   │   ├── auth.py        # GitHub OAuth and PAT management
│   │   │   ├── chat.py        # SSE streaming endpoint for Qwen-Agent
│   │   │   ├── repos.py       # Repository linking, syncing, and PR creation
│   │   │   └── sandbox.py     # Endpoints for code execution
│   │   ├── services/          # Business logic
│   │   │   ├── github_client.py # GitHub API wrapper (clone, commit, PR)
│   │   │   ├── parser.py      # Tree-sitter integration for JS/TS/Python/SQL
│   │   │   ├── embeddings.py  # Ollama embedding service wrapper
│   │   │   └── sandbox_mgr.py # Docker-in-Docker manager for ephemeral code execution
│   │   └── agents/            # Qwen-Agent orchestration
│   │       ├── qwen_assistant.py # Initialization of Qwen-Agent with tools
│   │       └── tools.py       # Custom tool definitions (if extending beyond defaults)
│   └── scripts/               # Utility scripts
│       ├── init_db.py         # Script to create tables and pgvector extensions
│       └── test_ollama.py     # Simple script to verify Ollama connectivity
│
├── frontend/                  # Web Application (React/Vue/Svelte)
│   ├── Dockerfile             # Multi-stage build (build -> nginx serve)
│   ├── package.json           # Node dependencies (Monaco Editor, Markdown parser, etc.)
│   ├── vite.config.js         # Build tool configuration
│   ├── tsconfig.json          # TypeScript configuration
│   └── src/
│       ├── main.ts(x)         # Application entry point
│       ├── App.tsx            # Root component
│       ├── components/        # Reusable UI components
│       │   ├── Chat/          # ChatBubble, MessageList, TypingIndicator
│       │   ├── Editor/        # MonacoEditor wrapper, DiffViewer
│       │   ├── Repo/          # RepoSelector, SyncStatus, PRLink
│       │   └── Tools/         # ToolCallStatus (e.g., "Searching Web...", "Running Code...")
│       ├── services/          # External communication
│       │   ├── api.ts         # Standard REST API calls (auth, repos)
│       │   └── sse.ts         # Server-Sent Events stream handler for chat
│       ├── stores/            # State management (Zustand, Pinia, or Redux)
│       │   ├── authStore.ts
│       │   ├── chatStore.ts
│       │   └── repoStore.ts
│       └── assets/            # Static assets (CSS, icons, images)
│
└── sandbox/                   # Ephemeral Code Execution Environment
    └── Dockerfile             # Lightweight Python image (e.g., python:3.10-slim) 
                               # with pre-installed common data science libraries
