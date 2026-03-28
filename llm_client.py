import google.generativeai as genai
import os
from typing import List
from dotenv import load_dotenv
import logging
import asyncio
import time

load_dotenv()
logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        genai.configure(api_key=api_key)

        for m in genai.list_models():
            print(m.name)
        
        # Use gemini-1.5-flash for better free tier support
        # Models in order of preference:
        # - gemini-1.5-flash: Better free tier limits
        # - gemini-1.5-pro: More capable but lower free tier quota
        # - gemini-2.0-flash: Newest but very restricted free tier
        self.text_model = genai.GenerativeModel("models/gemini-2.5-flash")
        logger.info("Using gemini-pro    model for text generation")
        
        # Embedding model
        self.embedding_model = "models/gemini-embedding-001"
        logger.info("Using text-embedding-004 model for embeddings")
        
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
                async def embed_func():
                    return genai.embed_content(
                        model=self.embedding_model,
                        content=text,
                        task_type="retrieval_document"
                    )
                
                result = await self._retry_with_backoff(embed_func)
                embeddings.append(result['embedding'])
            except Exception as e:
                logger.warning(f"Embedding error: {e}. Using zero vector.")
                # Return zero vector on error
                embeddings.append([0.0] * 768)
        return embeddings

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text using the Gemini API with caching and retry logic."""
        # Create cache key
        cache_key = hash(system_prompt + user_prompt)
        
        # Check cache first
        if cache_key in self.response_cache:
            logger.info("Returning cached response")
            return self.response_cache[cache_key]
        
        try:
            async def generate_func():
                full_prompt = f"{system_prompt}\n\n{user_prompt}"
                response = self.text_model.generate_content(full_prompt)
                return response.text if response.text else "No response generated"
            
            result = await self._retry_with_backoff(generate_func, max_retries=4, initial_delay=2.0)
            
            # Cache the result
            self.response_cache[cache_key] = result
            logger.info("Successfully generated response")
            return result
            
        except Exception as e:
            logger.error(f"Generation error: {e}")
            error_response = f"Error: {str(e)}"
            
            # Cache even error responses to prevent repeated API calls
            self.response_cache[cache_key] = error_response
            return error_response