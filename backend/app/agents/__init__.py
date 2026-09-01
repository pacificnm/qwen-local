"""Qwen-Agent orchestration for chat turns (MASTER_SPEC §4.3).

The turn is driven by `qwen_agent.agents.Assistant` (OpenAI-compatible LLM
→ host Ollama) with the app's toolset; `qwen_assistant.run_turn` adapts the
synchronous Qwen-Agent loop to the SSE event contract consumed by
`app/api/chat.py` and the frontend.
"""

import os

# Qwen-Agent reads this at import time (qwen_agent/settings.py). Bound the
# per-run LLM calls: the old hand-rolled loop capped at 3 tool rounds.
# 50 total calls covers multi-step tasks (read → write → edit → verify →
# fix → verify) while still bounding a stuck model on a busy V100.
# (If the budget IS exhausted, `qwen_assistant.final_answer_stream` still
# forces a plain-text answer at the end of the turn, with one retry if the
# first attempt produces no text.)
os.environ.setdefault("QWEN_AGENT_MAX_LLM_CALL_PER_RUN", "50")
