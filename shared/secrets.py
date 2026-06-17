"""Secret management — provider-agnostic factory.

Seleziona il backend tramite env var SECRET_PROVIDER:
  local  (default) — legge da .env / variabili d'ambiente
  azure            — Azure Key Vault (azure-keyvault-secrets + azure-identity)
  aws              — AWS Secrets Manager (boto3)

Uso:
    from shared.secrets import get_secret
    api_key = get_secret("ANTHROPIC_API_KEY")

In locale il nome della chiave è il nome della variabile d'ambiente (es. ANTHROPIC_API_KEY).
In Azure Key Vault i nomi sono normalizzati in kebab-case (es. anthropic-api-key).
In AWS Secrets Manager il nome viene usato così com'è, a meno di override.
"""
import os
from functools import lru_cache
from typing import Callable


def _local_provider() -> Callable[[str], str]:
    def get(key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise KeyError(f"Secret '{key}' not found in environment variables.")
        return value
    return get


def _azure_provider() -> Callable[[str], str]:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        raise ImportError(
            "azure-identity and azure-keyvault-secrets are required for SECRET_PROVIDER=azure. "
            "Install with: uv add azure-identity azure-keyvault-secrets"
        )

    vault_url = os.getenv("AZURE_KEYVAULT_URL")
    if not vault_url:
        raise EnvironmentError("AZURE_KEYVAULT_URL must be set when SECRET_PROVIDER=azure.")

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    def _to_kv_name(key: str) -> str:
        return key.lower().replace("_", "-")

    def get(key: str) -> str:
        secret = client.get_secret(_to_kv_name(key))
        if secret.value is None:
            raise KeyError(f"Secret '{key}' found in Key Vault but has no value.")
        return secret.value

    return get


def _aws_provider() -> Callable[[str], str]:
    try:
        import boto3
        import json as _json
    except ImportError:
        raise ImportError(
            "boto3 is required for SECRET_PROVIDER=aws. "
            "Install with: uv add boto3"
        )

    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "eu-west-1"))
    client = boto3.client("secretsmanager", region_name=region)

    def get(key: str) -> str:
        response = client.get_secret_value(SecretId=key)
        raw = response.get("SecretString") or response.get("SecretBinary", b"").decode()
        # Se il secret è un JSON object, estrae il valore alla chiave 'value' o al nome stesso
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get(key) or parsed.get("value") or raw
        except (_json.JSONDecodeError, TypeError):
            pass
        return raw

    return get


@lru_cache(maxsize=1)
def _get_provider() -> Callable[[str], str]:
    provider = os.getenv("SECRET_PROVIDER", "local").lower()
    if provider == "local":
        return _local_provider()
    elif provider == "azure":
        return _azure_provider()
    elif provider == "aws":
        return _aws_provider()
    else:
        raise ValueError(
            f"Unknown SECRET_PROVIDER='{provider}'. Valid values: local, azure, aws."
        )


def get_secret(key: str) -> str:
    """Retrieve a secret by name from the configured provider.

    Args:
        key: Secret name, e.g. "ANTHROPIC_API_KEY" or "A2A_SHARED_SECRET"

    Returns:
        Secret value as string.

    Raises:
        KeyError: Secret not found.
        EnvironmentError: Provider misconfigured.
    """
    return _get_provider()(key)
