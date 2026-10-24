"""Built-in model provider adapters."""

from codeatlas.adapters.model.ollama import PROVIDER_ID, OllamaModelProvider
from codeatlas.adapters.model.plugin import OLLAMA_CAPABILITY, OllamaModelPlugin

__all__ = [
    "OLLAMA_CAPABILITY",
    "PROVIDER_ID",
    "OllamaModelPlugin",
    "OllamaModelProvider",
]
