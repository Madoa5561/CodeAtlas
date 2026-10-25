"""Built-in model provider adapters."""

from codeatlas.adapters.model.anthropic import AnthropicModelProvider
from codeatlas.adapters.model.deepseek import PROVIDER_ID as DEEPSEEK_PROVIDER_ID
from codeatlas.adapters.model.deepseek import DeepSeekModelProvider
from codeatlas.adapters.model.gemini import GeminiModelProvider
from codeatlas.adapters.model.ollama import PROVIDER_ID as OLLAMA_PROVIDER_ID
from codeatlas.adapters.model.ollama import OllamaModelProvider
from codeatlas.adapters.model.openai import OpenAIModelProvider
from codeatlas.adapters.model.openai_compatible import OpenAICompatibleModelProvider
from codeatlas.adapters.model.plugin import OLLAMA_CAPABILITY, OllamaModelPlugin
from codeatlas.adapters.model.plugin_deepseek import (
    DEEPSEEK_CAPABILITY,
    DeepSeekModelPlugin,
)
from codeatlas.adapters.model.plugin_external import (
    ExternalModelPlugin,
    build_external_plugin,
)

__all__ = [
    "DEEPSEEK_CAPABILITY",
    "DEEPSEEK_PROVIDER_ID",
    "OLLAMA_CAPABILITY",
    "OLLAMA_PROVIDER_ID",
    "AnthropicModelProvider",
    "DeepSeekModelPlugin",
    "DeepSeekModelProvider",
    "ExternalModelPlugin",
    "GeminiModelProvider",
    "OllamaModelPlugin",
    "OllamaModelProvider",
    "OpenAICompatibleModelProvider",
    "OpenAIModelProvider",
    "build_external_plugin",
]
