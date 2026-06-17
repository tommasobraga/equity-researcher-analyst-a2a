"""Provider-agnostic LLM client factory.

Restituisce un client Anthropic SDK compatibile in base a LLM_PROVIDER:

  local   — anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)  [solo dev/test personale]
  bedrock — anthropic.AnthropicBedrock()                    [credenziali via boto3/IAM]
  vertex  — anthropic.AnthropicVertex(region, project_id)   [credenziali via ADC]
  azure   — LiteLLM proxy su Azure AI Foundry               [credenziali via azure-identity]

Il client viene costruito una sola volta (module-level singleton).
In DEMO_MODE il client non viene mai invocato — la factory gira comunque
per intercettare configurazioni errate prima che la pipeline parta.
"""
import os
from functools import lru_cache
from typing import Union

import anthropic


def _build_client() -> Union[anthropic.Anthropic, anthropic.AnthropicBedrock, anthropic.AnthropicVertex]:
    provider = os.getenv("LLM_PROVIDER", "local").lower()

    if provider == "bedrock":
        region = os.getenv("AWS_REGION", "us-east-1")
        return anthropic.AnthropicBedrock(aws_region=region)

    if provider == "vertex":
        region = os.getenv("VERTEX_REGION", "us-east5")
        project_id = os.getenv("VERTEX_PROJECT_ID")
        if not project_id:
            raise EnvironmentError("LLM_PROVIDER=vertex richiede VERTEX_PROJECT_ID")
        return anthropic.AnthropicVertex(region=region, project_id=project_id)

    if provider == "azure":
        # Azure AI Foundry non ha un client SDK nativo nell'SDK Anthropic.
        # Il path approvato è LiteLLM con azure-identity credential injection.
        # Placeholder — da completare quando le credenziali Azure sono disponibili.
        raise NotImplementedError(
            "LLM_PROVIDER=azure non ancora implementato. "
            "Vedi enterprise-evolution-plan.md Fase 0b per il piano di integrazione Azure AI Foundry."
        )

    if provider == "local":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "LLM_PROVIDER=local richiede ANTHROPIC_API_KEY. "
                "In ambiente enterprise usa LLM_PROVIDER=bedrock|vertex|azure. "
                "In sviluppo locale usa DEMO_MODE=true per evitare chiamate LLM."
            )
        return anthropic.Anthropic(api_key=api_key)

    raise ValueError(
        f"LLM_PROVIDER='{provider}' non riconosciuto. "
        "Valori validi: local, bedrock, vertex, azure."
    )


@lru_cache(maxsize=1)
def get_llm_client() -> Union[anthropic.Anthropic, anthropic.AnthropicBedrock, anthropic.AnthropicVertex]:
    """Return the singleton LLM client for the configured provider."""
    return _build_client()
