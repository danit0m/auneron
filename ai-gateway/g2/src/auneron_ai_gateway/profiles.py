"""Perfis fixos. O cliente escolhe um perfil, nunca um modelo livre.

Cada perfil fixa: model_id, digest, think, amostragem, num_ctx, num_predict e teto de saida.
Apenas os perfis listados em [profiles].enabled do TOML aceitam requisicoes (padrao: so o aprovado).
"""

from __future__ import annotations

from dataclasses import dataclass

PROFILE_ID = "qwen3-4b-instruct-2507"
PROFILE_THINK_ID = "qwen3-4b-think"
DEFAULT_ENABLED = (PROFILE_ID,)


@dataclass(frozen=True)
class Profile:
    profile_id: str
    model_id: str
    digest: str  # sha256 hex, sem prefixo
    think: bool
    temperature: float
    top_p: float
    top_k: int
    seed: int
    num_ctx: int
    num_predict: int
    max_output_bytes: int
    keep_alive: str


PROFILES: dict[str, Profile] = {
    PROFILE_ID: Profile(
        profile_id=PROFILE_ID,
        model_id="qwen3:4b-instruct-2507-q4_K_M",
        digest="0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0",
        think=False,
        temperature=0,
        top_p=1.0,
        top_k=1,
        seed=0,
        num_ctx=8192,
        num_predict=1024,
        max_output_bytes=64 * 1024,
        keep_alive="5m",
    ),
    # definido, mas DESLIGADO por padrao
    PROFILE_THINK_ID: Profile(
        profile_id=PROFILE_THINK_ID,
        model_id="qwen3:4b",
        digest="359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
        think=True,
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        seed=0,
        num_ctx=8192,
        num_predict=4096,
        max_output_bytes=64 * 1024,
        keep_alive="5m",
    ),
}

PROFILE_NAMES = tuple(PROFILES)
