"""Dependency injection for routes."""

from functools import lru_cache

from app.services.llm_service import LLMService


@lru_cache()
def get_llm_service() -> LLMService:
    return LLMService()
