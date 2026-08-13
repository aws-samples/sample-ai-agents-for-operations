"""
Bedrock Model Reference - Auto-generated mapping of friendly names to model IDs.

This module provides:
- MODEL_CATALOG: Complete model information with modalities
- FRIENDLY_NAME_MAP: Quick lookup from friendly names to model IDs
- Helper functions for model resolution
"""

# Complete model catalog with metadata
MODEL_CATALOG = {
    # ============ ANTHROPIC CLAUDE ============
    "anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "name": "Claude Sonnet 4.5",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude sonnet 4.5", "sonnet 4.5", "claude 4.5 sonnet"]
    },
    "anthropic.claude-sonnet-4-20250514-v1:0": {
        "name": "Claude Sonnet 4",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude sonnet 4", "sonnet 4", "claude 4 sonnet"]
    },
    "anthropic.claude-opus-4-5-20251101-v1:0": {
        "name": "Claude Opus 4.5",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude opus 4.5", "opus 4.5", "claude 4.5 opus"]
    },
    "anthropic.claude-opus-4-1-20250805-v1:0": {
        "name": "Claude Opus 4.1",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude opus 4.1", "opus 4.1"]
    },
    "anthropic.claude-opus-4-20250514-v1:0": {
        "name": "Claude Opus 4",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude opus 4", "opus 4", "claude 4 opus"]
    },
    "anthropic.claude-haiku-4-5-20251001-v1:0": {
        "name": "Claude Haiku 4.5",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude haiku 4.5", "haiku 4.5", "claude 4.5 haiku"]
    },
    "anthropic.claude-3-7-sonnet-20250219-v1:0": {
        "name": "Claude 3.7 Sonnet",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude 3.7 sonnet", "claude 3.7", "sonnet 3.7"]
    },
    "anthropic.claude-3-5-sonnet-20241022-v2:0": {
        "name": "Claude 3.5 Sonnet v2",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude 3.5 sonnet v2", "claude 3.5 sonnet", "sonnet 3.5"]
    },
    "anthropic.claude-3-5-haiku-20241022-v1:0": {
        "name": "Claude 3.5 Haiku",
        "provider": "Anthropic",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["claude 3.5 haiku", "haiku 3.5"]
    },
    "anthropic.claude-3-opus-20240229-v1:0": {
        "name": "Claude 3 Opus",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude 3 opus", "opus 3"]
    },
    "anthropic.claude-3-sonnet-20240229-v1:0": {
        "name": "Claude 3 Sonnet",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude 3 sonnet", "sonnet 3"]
    },
    "anthropic.claude-3-haiku-20240307-v1:0": {
        "name": "Claude 3 Haiku",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude 3 haiku", "haiku 3"]
    },
    
    # ============ AMAZON NOVA ============
    "amazon.nova-premier-v1:0": {
        "name": "Nova Premier",
        "provider": "Amazon",
        "input": ["TEXT", "IMAGE", "VIDEO"],
        "output": ["TEXT"],
        "aliases": ["nova premier", "amazon nova premier"]
    },
    "amazon.nova-pro-v1:0": {
        "name": "Nova Pro",
        "provider": "Amazon",
        "input": ["TEXT", "IMAGE", "VIDEO"],
        "output": ["TEXT"],
        "aliases": ["nova pro", "amazon nova pro"]
    },
    "amazon.nova-lite-v1:0": {
        "name": "Nova Lite",
        "provider": "Amazon",
        "input": ["TEXT", "IMAGE", "VIDEO"],
        "output": ["TEXT"],
        "aliases": ["nova lite", "amazon nova lite"]
    },
    "amazon.nova-micro-v1:0": {
        "name": "Nova Micro",
        "provider": "Amazon",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["nova micro", "amazon nova micro"]
    },
    "amazon.nova-2-lite-v1:0": {
        "name": "Nova 2 Lite",
        "provider": "Amazon",
        "input": ["TEXT", "IMAGE", "VIDEO"],
        "output": ["TEXT"],
        "aliases": ["nova 2 lite", "nova 2"]
    },
    "amazon.nova-2-sonic-v1:0": {
        "name": "Nova 2 Sonic",
        "provider": "Amazon",
        "input": ["SPEECH"],
        "output": ["SPEECH", "TEXT"],
        "aliases": ["nova sonic", "nova 2 sonic"]
    },
    
    # ============ META LLAMA ============
    "meta.llama4-maverick-17b-instruct-v1:0": {
        "name": "Llama 4 Maverick 17B",
        "provider": "Meta",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["llama 4 maverick", "llama4 maverick"]
    },
    "meta.llama4-scout-17b-instruct-v1:0": {
        "name": "Llama 4 Scout 17B",
        "provider": "Meta",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["llama 4 scout", "llama4 scout"]
    },
    "meta.llama3-3-70b-instruct-v1:0": {
        "name": "Llama 3.3 70B",
        "provider": "Meta",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["llama 3.3", "llama 3.3 70b"]
    },
    "meta.llama3-2-90b-instruct-v1:0": {
        "name": "Llama 3.2 90B",
        "provider": "Meta",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["llama 3.2 90b", "llama 3.2"]
    },
    "meta.llama3-1-405b-instruct-v1:0": {
        "name": "Llama 3.1 405B",
        "provider": "Meta",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["llama 3.1 405b", "llama 405b"]
    },
    "meta.llama3-1-70b-instruct-v1:0": {
        "name": "Llama 3.1 70B",
        "provider": "Meta",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["llama 3.1 70b"]
    },
    
    # ============ DEEPSEEK ============
    "deepseek.r1-v1:0": {
        "name": "DeepSeek R1",
        "provider": "DeepSeek",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["deepseek r1", "deepseek-r1"]
    },
    "deepseek.v3-v1:0": {
        "name": "DeepSeek V3.1",
        "provider": "DeepSeek",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["deepseek v3", "deepseek-v3"]
    },
    
    # ============ MISTRAL ============
    "mistral.mistral-large-3-675b-instruct": {
        "name": "Mistral Large 3",
        "provider": "Mistral AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["mistral large 3", "mistral large"]
    },
    "mistral.pixtral-large-2502-v1:0": {
        "name": "Pixtral Large",
        "provider": "Mistral AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["pixtral large", "pixtral"]
    },
    "mistral.magistral-small-2509": {
        "name": "Magistral Small",
        "provider": "Mistral AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["magistral small", "magistral"]
    },
    
    # ============ COHERE ============
    "cohere.command-r-plus-v1:0": {
        "name": "Command R+",
        "provider": "Cohere",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["command r+", "cohere command r+"]
    },
    "cohere.command-r-v1:0": {
        "name": "Command R",
        "provider": "Cohere",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["command r", "cohere command r"]
    },
    
    # ============ QWEN ============
    "qwen.qwen3-235b-a22b-2507-v1:0": {
        "name": "Qwen3 235B",
        "provider": "Qwen",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["qwen3 235b", "qwen 235b"]
    },
    "qwen.qwen3-coder-480b-a35b-v1:0": {
        "name": "Qwen3 Coder 480B",
        "provider": "Qwen",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["qwen3 coder", "qwen coder"]
    },
    
    # ============ GOOGLE ============
    "google.gemma-3-27b-it": {
        "name": "Gemma 3 27B",
        "provider": "Google",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["gemma 3 27b", "gemma 3"]
    },
    
    # ============ EMBEDDINGS ============
    "amazon.titan-embed-text-v2:0": {
        "name": "Titan Text Embeddings V2",
        "provider": "Amazon",
        "input": ["TEXT"],
        "output": ["EMBEDDING"],
        "aliases": ["titan embeddings", "titan embed"]
    },
    "cohere.embed-v4:0": {
        "name": "Cohere Embed V4",
        "provider": "Cohere",
        "input": ["TEXT", "IMAGE"],
        "output": ["EMBEDDING"],
        "aliases": ["cohere embed", "embed v4"]
    },
    
    # ============ ANTHROPIC CLAUDE (NEW) ============
    "anthropic.claude-sonnet-4-6": {
        "name": "Claude Sonnet 4.6",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude sonnet 4.6", "sonnet 4.6", "claude 4.6 sonnet"]
    },
    "anthropic.claude-sonnet-5": {
        "name": "Claude Sonnet 5",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude sonnet 5", "sonnet 5"]
    },
    "anthropic.claude-opus-4-6-v1": {
        "name": "Claude Opus 4.6",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude opus 4.6", "opus 4.6"]
    },
    "anthropic.claude-opus-4-7": {
        "name": "Claude Opus 4.7",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude opus 4.7", "opus 4.7"]
    },
    "anthropic.claude-opus-4-8": {
        "name": "Claude Opus 4.8",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude opus 4.8", "opus 4.8"]
    },
    "anthropic.claude-fable-5": {
        "name": "Claude Fable 5",
        "provider": "Anthropic",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["claude fable 5", "fable 5"]
    },
    
    # ============ DEEPSEEK (NEW) ============
    "deepseek.v3.2": {
        "name": "DeepSeek V3.2",
        "provider": "DeepSeek",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["deepseek v3.2", "deepseek-v3.2"]
    },
    
    # ============ GOOGLE (NEW) ============
    "google.gemma-3-12b-it": {
        "name": "Gemma 3 12B",
        "provider": "Google",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["gemma 3 12b", "gemma 12b"]
    },
    "google.gemma-3-4b-it": {
        "name": "Gemma 3 4B",
        "provider": "Google",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["gemma 3 4b", "gemma 4b"]
    },
    
    # ============ META (NEW) ============
    "meta.llama3-1-8b-instruct-v1:0": {
        "name": "Llama 3.1 8B",
        "provider": "Meta",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["llama 3.1 8b"]
    },
    "meta.llama3-70b-instruct-v1:0": {
        "name": "Llama 3 70B",
        "provider": "Meta",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["llama 3 70b"]
    },
    "meta.llama3-8b-instruct-v1:0": {
        "name": "Llama 3 8B",
        "provider": "Meta",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["llama 3 8b"]
    },
    
    # ============ MINIMAX ============
    "minimax.minimax-m2": {
        "name": "MiniMax M2",
        "provider": "MiniMax",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["minimax m2", "m2"]
    },
    "minimax.minimax-m2.1": {
        "name": "MiniMax M2.1",
        "provider": "MiniMax",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["minimax m2.1", "m2.1"]
    },
    "minimax.minimax-m2.5": {
        "name": "MiniMax M2.5",
        "provider": "MiniMax",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["minimax m2.5", "m2.5"]
    },
    
    # ============ MISTRAL (NEW) ============
    "mistral.devstral-2-123b": {
        "name": "Devstral 2 123B",
        "provider": "Mistral AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["devstral 2", "devstral 123b"]
    },
    "mistral.ministral-3-14b-instruct": {
        "name": "Ministral 14B",
        "provider": "Mistral AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["ministral 14b", "ministral 3 14b"]
    },
    "mistral.ministral-3-3b-instruct": {
        "name": "Ministral 3B",
        "provider": "Mistral AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["ministral 3b"]
    },
    "mistral.ministral-3-8b-instruct": {
        "name": "Ministral 8B",
        "provider": "Mistral AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["ministral 8b", "ministral 3 8b"]
    },
    "mistral.mistral-7b-instruct-v0:2": {
        "name": "Mistral 7B Instruct",
        "provider": "Mistral AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["mistral 7b", "mistral 7b instruct"]
    },
    "mistral.mistral-large-2402-v1:0": {
        "name": "Mistral Large (24.02)",
        "provider": "Mistral AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["mistral large 24.02", "mistral large 2402"]
    },
    "mistral.mistral-small-2402-v1:0": {
        "name": "Mistral Small (24.02)",
        "provider": "Mistral AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["mistral small", "mistral small 24.02"]
    },
    "mistral.mixtral-8x7b-instruct-v0:1": {
        "name": "Mixtral 8x7B",
        "provider": "Mistral AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["mixtral", "mixtral 8x7b"]
    },
    "mistral.voxtral-mini-3b-2507": {
        "name": "Voxtral Mini 3B",
        "provider": "Mistral AI",
        "input": ["SPEECH", "TEXT"],
        "output": ["TEXT"],
        "aliases": ["voxtral mini", "voxtral 3b"]
    },
    "mistral.voxtral-small-24b-2507": {
        "name": "Voxtral Small 24B",
        "provider": "Mistral AI",
        "input": ["SPEECH", "TEXT"],
        "output": ["TEXT"],
        "aliases": ["voxtral small", "voxtral 24b"]
    },
    
    # ============ MOONSHOT AI ============
    "moonshot.kimi-k2-thinking": {
        "name": "Kimi K2 Thinking",
        "provider": "Moonshot AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["kimi k2", "kimi k2 thinking"]
    },
    "moonshotai.kimi-k2.5": {
        "name": "Kimi K2.5",
        "provider": "Moonshot AI",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["kimi k2.5"]
    },
    
    # ============ NVIDIA ============
    "nvidia.nemotron-nano-12b-v2": {
        "name": "Nemotron Nano 12B",
        "provider": "NVIDIA",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["nemotron nano 12b", "nemotron 12b"]
    },
    "nvidia.nemotron-nano-3-30b": {
        "name": "Nemotron Nano 3 30B",
        "provider": "NVIDIA",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["nemotron nano 30b", "nemotron 30b"]
    },
    "nvidia.nemotron-nano-9b-v2": {
        "name": "Nemotron Nano 9B",
        "provider": "NVIDIA",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["nemotron nano 9b", "nemotron 9b"]
    },
    "nvidia.nemotron-super-3-120b": {
        "name": "Nemotron Super 120B",
        "provider": "NVIDIA",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["nemotron super", "nemotron 120b"]
    },
    
    # ============ OPENAI ============
    "openai.gpt-oss-120b-1:0": {
        "name": "GPT OSS 120B",
        "provider": "OpenAI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["gpt oss 120b"]
    },
    "openai.gpt-oss-20b-1:0": {
        "name": "GPT OSS 20B",
        "provider": "OpenAI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["gpt oss 20b"]
    },
    "openai.gpt-oss-safeguard-120b": {
        "name": "GPT OSS Safeguard 120B",
        "provider": "OpenAI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["gpt safeguard 120b"]
    },
    "openai.gpt-oss-safeguard-20b": {
        "name": "GPT OSS Safeguard 20B",
        "provider": "OpenAI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["gpt safeguard 20b"]
    },
    
    # ============ QWEN (NEW) ============
    "qwen.qwen3-32b-v1:0": {
        "name": "Qwen3 32B",
        "provider": "Qwen",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["qwen3 32b", "qwen 32b"]
    },
    "qwen.qwen3-coder-30b-a3b-v1:0": {
        "name": "Qwen3 Coder 30B",
        "provider": "Qwen",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["qwen3 coder 30b", "qwen coder 30b"]
    },
    "qwen.qwen3-coder-next": {
        "name": "Qwen3 Coder Next",
        "provider": "Qwen",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["qwen3 coder next", "qwen coder next"]
    },
    "qwen.qwen3-next-80b-a3b": {
        "name": "Qwen3 Next 80B",
        "provider": "Qwen",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["qwen3 next", "qwen next 80b"]
    },
    "qwen.qwen3-vl-235b-a22b": {
        "name": "Qwen3 VL 235B",
        "provider": "Qwen",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["qwen3 vl", "qwen vl 235b"]
    },
    
    # ============ WRITER ============
    "writer.palmyra-vision-7b": {
        "name": "Palmyra Vision 7B",
        "provider": "Writer",
        "input": ["TEXT", "IMAGE"],
        "output": ["TEXT"],
        "aliases": ["palmyra vision", "palmyra vision 7b"]
    },
    "writer.palmyra-x4-v1:0": {
        "name": "Palmyra X4",
        "provider": "Writer",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["palmyra x4"]
    },
    "writer.palmyra-x5-v1:0": {
        "name": "Palmyra X5",
        "provider": "Writer",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["palmyra x5"]
    },
    
    # ============ Z.AI ============
    "zai.glm-4.7": {
        "name": "GLM 4.7",
        "provider": "Z.AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["glm 4.7"]
    },
    "zai.glm-4.7-flash": {
        "name": "GLM 4.7 Flash",
        "provider": "Z.AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["glm 4.7 flash"]
    },
    "zai.glm-5": {
        "name": "GLM 5",
        "provider": "Z.AI",
        "input": ["TEXT"],
        "output": ["TEXT"],
        "aliases": ["glm 5"]
    },
}

# Build reverse lookup: friendly name -> model ID
FRIENDLY_NAME_MAP = {}
for model_id, info in MODEL_CATALOG.items():
    # Add the official name
    FRIENDLY_NAME_MAP[info["name"].lower()] = model_id
    # Add all aliases
    for alias in info.get("aliases", []):
        FRIENDLY_NAME_MAP[alias.lower()] = model_id


def resolve_model_id(user_input: str) -> str | None:
    """
    Resolve a user-friendly model name to the full model ID.
    
    Args:
        user_input: User's model name (e.g., "claude sonnet 4.5", "nova pro")
    
    Returns:
        Full model ID or None if not found
    """
    normalized = user_input.lower().strip()
    
    # Direct match
    if normalized in FRIENDLY_NAME_MAP:
        return FRIENDLY_NAME_MAP[normalized]
    
    # Check if it's already a valid model ID
    if normalized in MODEL_CATALOG:
        return normalized
    
    # Fuzzy match - find best partial match
    for name, model_id in FRIENDLY_NAME_MAP.items():
        if normalized in name or name in normalized:
            return model_id
    
    return None


def get_model_info(model_id: str) -> dict | None:
    """Get full model information by ID."""
    return MODEL_CATALOG.get(model_id)


def list_models_by_provider(provider: str) -> list[str]:
    """List all model IDs for a given provider."""
    return [
        model_id for model_id, info in MODEL_CATALOG.items()
        if info["provider"].lower() == provider.lower()
    ]
