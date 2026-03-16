"""LocalLLMEngine — GPU-accelerated local text generation.

Backend priority:
1. MLX (macOS Apple Silicon — fastest, Metal GPU via Apple framework)
2. transformers + CUDA (Linux/Windows with NVIDIA GPU)
3. No GPU → refuse to load (fall back to remote LLM via llm.py)

Model auto-selection based on available memory:
  Apple Silicon (MLX, uses 4-bit quantized models for speed):
    >=32 GB → mlx-community/Qwen3.5-9B-4bit
    >=16 GB → mlx-community/Qwen3.5-4B-MLX-4bit
    >= 8 GB → mlx-community/Qwen3.5-2B-OptiQ-4bit
  CUDA VRAM:
    >=16 GB → Qwen/Qwen3.5-9B
    >= 8 GB → Qwen/Qwen3.5-4B
    >= 4 GB → Qwen/Qwen3.5-2B

Override with CLICKMEM_LOCAL_MODEL env var.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import threading

logger = logging.getLogger(__name__)

_MLX_THRESHOLDS = [
    (32, "mlx-community/Qwen3.5-9B-4bit"),
    (16, "mlx-community/Qwen3.5-4B-MLX-4bit"),
    (8, "mlx-community/Qwen3.5-2B-OptiQ-4bit"),
]
_CUDA_THRESHOLDS = [
    (16, "Qwen/Qwen3.5-9B"),
    (8, "Qwen/Qwen3.5-4B"),
    (4, "Qwen/Qwen3.5-2B"),
]


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks and plain-text thinking preamble.

    Qwen3.5 4-bit quantized models sometimes emit ``Thinking Process:\\n...``
    instead of ``<think>`` tags.  After stripping XML tags, if the remaining
    text starts with non-JSON content we skip ahead to the first ``[`` or ``{``.
    """
    # 1. Remove <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # 2. If remaining text starts with non-JSON (plain-text thinking), skip to first JSON
    if text and text[0] not in "{[":
        match = re.search(r"[\[{]", text)
        if match:
            text = text[match.start():]
    return text.strip()


def _get_system_memory_gb() -> float:
    """Total physical memory in GB."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024**3)
    except (ValueError, OSError):
        return 0


def _get_cuda_vram_gb() -> float:
    """CUDA GPU VRAM in GB, or 0 if unavailable."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / (1024**3)
    except (ImportError, RuntimeError):
        pass
    return 0


def _auto_select_model() -> str | None:
    """Pick the best model for current hardware, or None if no GPU."""
    is_apple_silicon = (
        platform.system() == "Darwin" and platform.machine() == "arm64"
    )

    if is_apple_silicon:
        try:
            import mlx_lm  # noqa: F401
            mem_gb = _get_system_memory_gb()
            for threshold, model in _MLX_THRESHOLDS:
                if mem_gb >= threshold:
                    logger.info(
                        "Auto-selected %s for %.0f GB Apple Silicon (MLX)",
                        model, mem_gb,
                    )
                    return model
            logger.warning(
                "Apple Silicon with only %.0f GB — too little for local LLM",
                mem_gb,
            )
        except ImportError:
            logger.warning(
                "Apple Silicon detected but mlx-lm not installed. "
                "Reinstall: pip install clickmem"
            )
        return None

    vram_gb = _get_cuda_vram_gb()
    if vram_gb > 0:
        for threshold, model in _CUDA_THRESHOLDS:
            if vram_gb >= threshold:
                logger.info(
                    "Auto-selected %s for %.1f GB CUDA VRAM", model, vram_gb,
                )
                return model
        logger.warning("CUDA GPU with only %.1f GB VRAM — too little for local LLM", vram_gb)
        return None

    logger.info("No GPU detected — local LLM disabled")
    return None


class LocalLLMEngine:
    """Local LLM inference engine with automatic backend and model selection.

    Requires GPU acceleration (MLX on Apple Silicon, CUDA on Linux/Windows).
    CPU-only systems should use remote LLM via CLICKMEM_LLM_MODE=remote.
    """

    def __init__(
        self,
        model_name: str | None = None,
        max_tokens: int | None = None,
    ):
        self._explicit_model = model_name or os.environ.get("CLICKMEM_LOCAL_MODEL")
        self._model_name: str | None = self._explicit_model
        self._max_tokens = max_tokens or int(
            os.environ.get("CLICKMEM_LLM_MAX_TOKENS", "4096")
        )
        self._backend: str | None = None
        self._generate_fn = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """Load the model with GPU auto-detection."""
        if self._model_name is None:
            self._model_name = _auto_select_model()
            if self._model_name is None:
                raise RuntimeError(
                    "No GPU available for local LLM inference. "
                    "ClickMem requires Apple Silicon (MLX) or NVIDIA CUDA for local models. "
                    "On CPU-only systems, use a remote LLM provider: "
                    "pip install 'clickmem[llm]' and set CLICKMEM_LLM_MODE=remote"
                )

        errors: list[str] = []
        for name, loader in [("mlx", self._try_mlx), ("transformers", self._try_transformers)]:
            try:
                loader()
                return
            except ImportError as exc:
                errors.append(f"{name}: missing dependency — {exc}")
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        raise RuntimeError(
            f"No LLM backend available for {self._model_name}. "
            "Install mlx-lm (macOS Apple Silicon) or transformers+torch (CUDA).\n"
            + "\n".join(errors)
        )

    @property
    def backend(self) -> str:
        return self._backend or "none"

    @property
    def model_name(self) -> str:
        return self._model_name or "none"

    _TIMEOUT = int(os.environ.get("CLICKMEM_LLM_TIMEOUT", "120"))

    def complete(self, prompt: str) -> str:
        """Generate a completion for the given prompt.

        MLX Metal backend is not thread-safe — serialize all inference calls.
        Logs a warning if inference exceeds the timeout threshold.
        """
        assert self._generate_fn is not None, "Call load() first"
        import time
        with self._lock:
            t0 = time.monotonic()
            raw = self._generate_fn(prompt)
            elapsed = time.monotonic() - t0
            if elapsed > self._TIMEOUT:
                logger.warning("LLM call took %.1fs (threshold %ds)", elapsed, self._TIMEOUT)
        return _strip_think_tags(raw).strip()

    # ------------------------------------------------------------------
    # Backend loaders
    # ------------------------------------------------------------------

    def _try_mlx(self) -> None:
        from mlx_lm import generate as mlx_generate
        from mlx_lm import load as mlx_load

        model, tokenizer = mlx_load(self._model_name)

        max_tok = self._max_tokens

        try:
            from mlx_lm.sample_utils import make_sampler
            sampler = make_sampler(temp=0.0)
        except ImportError:
            sampler = None

        def _generate(prompt: str) -> str:
            messages = [{"role": "user", "content": prompt}]
            template_kwargs: dict = {
                "tokenize": False,
                "add_generation_prompt": True,
            }
            try:
                formatted = tokenizer.apply_chat_template(
                    messages, enable_thinking=False, **template_kwargs,
                )
            except TypeError:
                formatted = tokenizer.apply_chat_template(messages, **template_kwargs)
            kwargs: dict = {"max_tokens": max_tok}
            if sampler is not None:
                kwargs["sampler"] = sampler
            return mlx_generate(model, tokenizer, prompt=formatted, **kwargs)

        self._generate_fn = _generate
        self._backend = "mlx"
        logger.info("Local LLM loaded via MLX: %s", self._model_name)

    def _try_transformers(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            # MPS deadlocks in worker threads; CPU is too slow for 2B+ models.
            raise RuntimeError(
                "transformers backend requires CUDA. "
                "On Apple Silicon, install mlx-lm: pip install clickmem"
            )

        device = "cuda"
        dtype = torch.float16

        tokenizer = AutoTokenizer.from_pretrained(self._model_name)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            self._model_name, torch_dtype=dtype
        ).to(device)
        model.eval()

        max_tok = self._max_tokens

        def _generate(prompt: str) -> str:
            messages = [{"role": "user", "content": prompt}]
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = tokenizer(text, return_tensors="pt").to(device)
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=max_tok,
                    do_sample=False,
                )
            new_tokens = outputs[0][inputs["input_ids"].shape[-1] :]
            return tokenizer.decode(new_tokens, skip_special_tokens=True)

        self._generate_fn = _generate
        self._backend = "transformers"
        logger.info(
            "Local LLM loaded via transformers: %s on %s", self._model_name, device
        )
