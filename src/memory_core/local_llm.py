"""LocalLLMEngine — local text generation using MLX or transformers.

Backend priority:
1. MLX (macOS Apple Silicon — fastest, lowest memory)
2. transformers (cross-platform, installed via sentence-transformers)

Default model: Qwen/Qwen3.5-2B
Configurable via CLICKMEM_LOCAL_MODEL environment variable.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "Qwen/Qwen3.5-2B"


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning-mode models."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class LocalLLMEngine:
    """Local LLM inference engine with automatic backend selection.

    Tries MLX first (fast on Apple Silicon), then falls back to
    HuggingFace transformers (cross-platform).
    """

    def __init__(
        self,
        model_name: str | None = None,
        max_tokens: int = 1024,
    ):
        self._model_name = model_name or os.environ.get(
            "CLICKMEM_LOCAL_MODEL", _DEFAULT_MODEL
        )
        self._max_tokens = max_tokens
        self._backend: str | None = None
        self._generate_fn = None

    def load(self) -> None:
        """Load the model. Tries MLX, then transformers."""
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
            "Install mlx-lm (macOS) or transformers+torch.\n"
            + "\n".join(errors)
        )

    @property
    def backend(self) -> str:
        return self._backend or "none"

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(self, prompt: str) -> str:
        """Generate a completion for the given prompt."""
        assert self._generate_fn is not None, "Call load() first"
        raw = self._generate_fn(prompt)
        return _strip_think_tags(raw).strip()

    # ------------------------------------------------------------------
    # Backend loaders
    # ------------------------------------------------------------------

    def _try_mlx(self) -> None:
        from mlx_lm import generate as mlx_generate
        from mlx_lm import load as mlx_load

        model, tokenizer = mlx_load(self._model_name)

        max_tok = self._max_tokens

        # Build a greedy sampler (temperature=0) if the API supports it
        try:
            from mlx_lm.sample_utils import make_sampler
            sampler = make_sampler(temp=0.0)
        except ImportError:
            sampler = None

        def _generate(prompt: str) -> str:
            messages = [{"role": "user", "content": prompt}]
            formatted = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
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

        if torch.backends.mps.is_available():
            device = "mps"
            dtype = torch.float16
        elif torch.cuda.is_available():
            device = "cuda"
            dtype = torch.float16
        else:
            device = "cpu"
            dtype = torch.float32

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
