"""Gemini AI client using the new google.genai SDK."""
from google import genai
from google.genai import types
import os
from typing import List
from dotenv import load_dotenv
import logging
import asyncio

load_dotenv()
logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        # Initialize the new genai client
        self.client = genai.Client(api_key=api_key)

        # Models
        self.text_model = "gemini-2.5-flash"
        self.embedding_model = "gemini-embedding-001"
        logger.info(f"LLMClient initialized: text={self.text_model}, embed={self.embedding_model}")

        # Response cache to avoid repeated API calls
        self.response_cache = {}

    async def _retry_with_backoff(self, func, max_retries=3, initial_delay=1.0):
        """Retry a function with exponential backoff for rate limiting."""
        for attempt in range(max_retries):
            try:
                return await func()
            except Exception as e:
                error_msg = str(e)

                # Check if it's a rate limit error
                if "429" in error_msg or "quota" in error_msg.lower() or "rate limit" in error_msg.lower():
                    if attempt < max_retries - 1:
                        delay = initial_delay * (2 ** attempt)
                        logger.warning(f"Rate limited. Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue

                # For other errors, raise immediately
                raise

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        embeddings = []
        for text in texts:
            try:
                async def embed_func(t=text):
                    result = self.client.models.embed_content(
                        model=self.embedding_model,
                        contents=t,
                    )
                    return result

                result = await self._retry_with_backoff(embed_func)
                # New SDK returns result.embeddings[0].values
                embeddings.append(list(result.embeddings[0].values))
            except Exception as e:
                logger.warning(f"Embedding error: {e}. Using zero vector.")
                embeddings.append([0.0] * 768)
        return embeddings

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text using the Gemini API with caching and retry logic."""
        cache_key = hash(system_prompt + user_prompt)

        if cache_key in self.response_cache:
            logger.info("Returning cached response")
            return self.response_cache[cache_key]

        try:
            async def generate_func():
                response = self.client.models.generate_content(
                    model=self.text_model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                    ),
                )
                return response.text if response.text else "No response generated"

            result = await self._retry_with_backoff(generate_func, max_retries=4, initial_delay=2.0)

            self.response_cache[cache_key] = result
            logger.info("Successfully generated response")
            return result

        except Exception as e:
            logger.error(f"Generation error: {e}")
            raise