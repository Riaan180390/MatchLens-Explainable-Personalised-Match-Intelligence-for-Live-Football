"""Foundry connection settings and chat client factory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent_framework import BaseChatClient


@dataclass
class FoundrySettings:
    project_endpoint: str | None
    model: str | None
    auth: str = "default"  # "default" (DefaultAzureCredential) or "browser" (interactive sign-in)

    @property
    def configured(self) -> bool:
        return bool(self.project_endpoint and self.model)

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> FoundrySettings:
        if env_file and Path(env_file).exists():
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        return cls(
            project_endpoint=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
            model=os.getenv("FOUNDRY_MODEL"),
            auth=os.getenv("MATCHLENS_AUTH", "default"),
        )


def _credential(auth: str):
    if auth == "browser":
        from azure.identity import InteractiveBrowserCredential

        return InteractiveBrowserCredential()
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def make_chat_client(settings: FoundrySettings | None = None, offline: bool = False, **offline_kwargs) -> BaseChatClient:
    """A Foundry chat client, or the offline stand-in when asked for or when Foundry isn't configured."""
    settings = settings or FoundrySettings.from_env()
    if offline or not settings.configured:
        from .offline import OfflineChatClient

        return OfflineChatClient(**offline_kwargs)
    from agent_framework.foundry import FoundryChatClient

    return FoundryChatClient(
        project_endpoint=settings.project_endpoint,
        model=settings.model,
        credential=_credential(settings.auth),
    )
