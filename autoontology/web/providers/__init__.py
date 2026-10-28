# SPDX-License-Identifier: MIT
"""Chat providers behind the service's OpenAI-compatible ``/v1`` router.

``router.py`` forwards ``/v1/chat/completions`` to the selected chat provider: the Ollama
passthrough (default) or a generic OpenAI-compatible endpoint
(``AUTOONTOLOGY_CHAT_PROVIDER=openai``). Embeddings never come here (they go direct).
"""
