# Qwen 3.8:27B Skills & Capabilities for This Project

## Native Qwen-Agent Tools (Built-in)

### Code Interpreter
- **Use Case:** Execute Python scripts for data analysis, generate charts, validate logic
- **Skill Level:** High - Qwen 3.8 is specifically fine-tuned for tool-calling
- **Best Practice:** Let the model write and execute its own validation scripts rather than asking it to "imagine" the output

### Web Search
- **Use Case:** Fetch latest documentation, check API changes, research solutions
- **Skill Level:** Medium - Requires SearXNG or similar backend
- **Best Practice:** Use for real-time information (e.g., "What's the latest FastAPI version?") rather than general knowledge

### Function Calling
- **Use Case:** Structured tool use with JSON schema validation
- **Skill Level:** High - Native support via OpenAI-compatible endpoint
- **Best Practice:** Define clear tool schemas in your FastAPI backend; Qwen will reliably call them

---

## Context Management Strategies

### Chunking for RAG
- **Tree-sitter AST Parsing:** Critical for code - ensures functions/classes stay intact
- **Optimal Chunk Size:** 500-1000 tokens for code (preserves function boundaries)
- **Overlap:** 10-15% overlap between chunks to maintain context continuity
- **Metadata:** Always include file path, line numbers, and language in embeddings

### Prompt Engineering for Code Tasks
- **System Prompt Template:**
You are an expert software engineer working on {project_name}.
  Current repository: {repo_name}
  Relevant code context:
  {retrieved_chunks}
  Provide production-ready code with:

    Type hints (Python) or TypeScript types
    Error handling
    Brief inline comments for complex logic


- **Few-Shot Examples:** Include 1-2 examples of your code style in the system prompt
- **Chain-of-Thought:** For complex refactoring, ask the model to "think step-by-step" before generating code

---

## Development Workflow Skills

### Iterative Refinement Pattern
1. **First Pass:** Ask for high-level architecture/structure
2. **Second Pass:** Request specific implementation with context
3. **Third Pass:** Ask for error handling and edge cases
4. **Fourth Pass:** Request tests and documentation

### Code Review Simulation
- Ask Qwen to "review this code as a senior engineer" and identify:
  - Security vulnerabilities
  - Performance bottlenecks
  - Missing error handling
  - Test coverage gaps

### Debugging Workflow
- Provide error messages + relevant code context
- Ask: "What could cause this error? Provide 3 possible causes and solutions"
- Use Code Interpreter to validate hypotheses

---

## Model-Specific Optimizations

### Temperature Settings
- **Code Generation:** 0.2-0.4 (deterministic, precise)
- **Architecture Discussion:** 0.6-0.7 (creative but focused)
- **Documentation:** 0.5 (balanced)

### Max Tokens
- **Code Generation:** 2048-4096 (allows complete functions)
- **Chat/Explanation:** 1024 (prevents rambling)
- **Refactoring:** 4096+ (for large file rewrites)

### Context Window Utilization
- Qwen 3.8:27B supports 32k-128k context (depending on variant)
- **Strategy:** Fill 60-70% with retrieved code chunks, 20% with conversation history, 10-20% for response
- **Avoid:** Dumping entire files; use Tree-sitter to extract only relevant functions

---

## GitHub Integration Skills

### PR Description Generation
- Provide: diff summary, commit messages, related issue numbers
- Ask: "Generate a clear PR description with: summary, changes, testing notes"

### Code Review Comments
- Provide: file diff + project conventions
- Ask: "Review this diff and suggest improvements following our style guide"

### Commit Message Generation
- Provide: staged changes summary
- Ask: "Generate a conventional commit message (type: scope: description)"

---

## Performance Tips for V100

### Batch Processing
- For RAG ingestion: Process embeddings in batches of 8-16 chunks
- Use async/await in FastAPI to parallelize Ollama calls

### Model Selection
- **Fast Tasks** (simple Q&A, small edits): Use 7B variant if available
- **Complex Tasks** (architecture, refactoring): Use 27B variant
- Implement model routing in your backend based on task complexity

### Caching Strategy
- Cache frequent embeddings (e.g., common utility functions)
- Cache completed chat responses for identical prompts
- Use Redis or in-memory cache for hot data

---

## Limitations & Workarounds

### Known Limitations
- **Large File Context:** May struggle with files >500 lines
  - **Workaround:** Use Tree-sitter to extract only relevant sections
- **Multi-file Refactoring:** Can lose track across many files
  - **Workaround:** Break into sequential single-file tasks
- **Real-time Collaboration:** Not designed for live co-editing
  - **Workaround:** Use Monaco's diff view for async review

### When to Escalate
- If Qwen generates broken code 3+ times in a row, switch to a different approach
- For highly specialized domains (e.g., GPU kernel optimization), provide extensive context or use smaller, focused prompts
