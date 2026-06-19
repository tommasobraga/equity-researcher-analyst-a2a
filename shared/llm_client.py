"""Provider-agnostic LLM client factory.

Returns a compatible Anthropic SDK client based on LLM_PROVIDER:

  local   — anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)  [personal dev/test only]
  bedrock — anthropic.AnthropicBedrock()                    [credentials via boto3/IAM]
  vertex  — anthropic.AnthropicVertex(region, project_id)   [credentials via ADC]
  azure   — LiteLLM proxy on Azure AI Foundry               [credentials via azure-identity]

Client is built once (module-level singleton).
In DEMO_MODE the client is never invoked — factory still runs to catch
misconfigurations before the pipeline starts.
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
            raise EnvironmentError("LLM_PROVIDER=vertex requires VERTEX_PROJECT_ID")
        return anthropic.AnthropicVertex(region=region, project_id=project_id)

    if provider == "azure":
        # Azure AI Foundry has no native SDK client in the Anthropic SDK.
        # Approved path is LiteLLM with azure-identity credential injection.
        # Placeholder — complete when Azure credentials are available.
        raise NotImplementedError(
            "LLM_PROVIDER=azure not yet implemented. "
            "See enterprise-evolution-plan.md Phase 0b for the Azure AI Foundry integration plan."
        )

    if provider == "local":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "LLM_PROVIDER=local requires ANTHROPIC_API_KEY. "
                "In enterprise environments use LLM_PROVIDER=bedrock|vertex|azure. "
                "For local development use DEMO_MODE=true to avoid LLM calls."
            )
        return anthropic.Anthropic(api_key=api_key)

    raise ValueError(
        f"LLM_PROVIDER='{provider}' not recognised. "
        "Valid values: local, bedrock, vertex, azure."
    )


@lru_cache(maxsize=1)
def get_llm_client() -> Union[anthropic.Anthropic, anthropic.AnthropicBedrock, anthropic.AnthropicVertex]:
    """Return the singleton LLM client for the configured provider."""
    return _build_client()
