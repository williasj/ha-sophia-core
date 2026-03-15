# -*- coding: utf-8 -*-
"""
SOPHIA Core - Central coordinator and module registry
Handles module discovery, LLM client sharing, inter-module communication,
dashboard management, token tracking, web search, and RAG retrieval.
"""
import asyncio
import logging
from typing import Any, Dict, Set, Optional, Callable, List
from datetime import datetime, timedelta
import json
from collections import defaultdict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sophia_core"
PLATFORMS = ["sensor"]

# Event types
EVENT_MODULE_REGISTERED = f"{DOMAIN}_module_registered"
EVENT_MODULE_UNREGISTERED = f"{DOMAIN}_module_unregistered"
EVENT_MODULE_STATUS_CHANGE = f"{DOMAIN}_module_status_change"
EVENT_LLM_REQUEST = f"{DOMAIN}_llm_request"
EVENT_LLM_RESPONSE = f"{DOMAIN}_llm_response"
EVENT_DASHBOARD_UPDATED = f"{DOMAIN}_dashboard_updated"

# Dashboard constants
SOPHIA_DASHBOARD_URL = "sophia"

# Token estimation constants
AVG_CHARS_PER_TOKEN = 4

# Web search / RAG defaults
DEFAULT_SEARXNG_URL = "http://localhost:8713"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_TEI_URL = "http://localhost:8764"
DEFAULT_SEARCH_RESULTS = 5
DEFAULT_RAG_RESULTS = 5


class TokenUsageTracker:
    """Tracks token usage and cost estimation for LLM calls"""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0

        self.module_stats = defaultdict(lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "requests": 0,
            "last_request": None
        })

        self.request_history = []
        self.max_history = 100

        self.daily_input_tokens = 0
        self.daily_output_tokens = 0
        self.daily_requests = 0
        self.daily_reset_date = datetime.now().date()

        self.cost_per_1k_input_tokens = 0.0
        self.cost_per_1k_output_tokens = 0.0

        self.perf_history: List[float] = []
        self.max_perf_history = 100
        self.peak_tokens_per_second = 0.0
        self.min_tokens_per_second = float("inf")
        self.total_eval_duration_ns = 0
        self.total_prompt_eval_duration_ns = 0
        self.total_load_duration_ns = 0
        self.total_latency_ns = 0
        self.model_loads_detected = 0
        self.last_load_duration_ns = 0

        self._store = Store(hass, version=1, key=f"{DOMAIN}_token_usage")

    async def async_load(self):
        """Load saved token data from storage"""
        try:
            data = await self._store.async_load()
            if data:
                self.total_input_tokens = data.get("total_input_tokens", 0)
                self.total_output_tokens = data.get("total_output_tokens", 0)
                self.total_requests = data.get("total_requests", 0)

                module_stats_data = data.get("module_stats", {})
                self.module_stats = defaultdict(lambda: {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "requests": 0,
                    "last_request": None
                })
                for module_id, stats in module_stats_data.items():
                    self.module_stats[module_id] = stats

                self.request_history = data.get("request_history", [])

                self.perf_history = data.get("perf_history", [])
                self.peak_tokens_per_second = data.get("peak_tokens_per_second", 0.0)
                raw_min = data.get("min_tokens_per_second", float("inf"))
                self.min_tokens_per_second = raw_min if raw_min != 0 else float("inf")
                self.total_eval_duration_ns = data.get("total_eval_duration_ns", 0)
                self.total_prompt_eval_duration_ns = data.get("total_prompt_eval_duration_ns", 0)
                self.total_load_duration_ns = data.get("total_load_duration_ns", 0)
                self.total_latency_ns = data.get("total_latency_ns", 0)
                self.model_loads_detected = data.get("model_loads_detected", 0)
                self.last_load_duration_ns = data.get("last_load_duration_ns", 0)

                saved_date = data.get("daily_reset_date")
                if saved_date:
                    saved_date_obj = datetime.fromisoformat(saved_date).date()
                    if saved_date_obj == datetime.now().date():
                        self.daily_input_tokens = data.get("daily_input_tokens", 0)
                        self.daily_output_tokens = data.get("daily_output_tokens", 0)
                        self.daily_requests = data.get("daily_requests", 0)

                _LOGGER.info(
                    "Loaded token usage data: %d total tokens, %d requests",
                    self.total_input_tokens + self.total_output_tokens,
                    self.total_requests
                )
        except Exception as e:
            _LOGGER.warning("Could not load token usage data: %s", e)

    async def async_save(self):
        """Save token data to storage"""
        try:
            data = {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_requests": self.total_requests,
                "module_stats": dict(self.module_stats),
                "request_history": self.request_history,
                "daily_input_tokens": self.daily_input_tokens,
                "daily_output_tokens": self.daily_output_tokens,
                "daily_requests": self.daily_requests,
                "daily_reset_date": self.daily_reset_date.isoformat(),
                "perf_history": self.perf_history[-self.max_perf_history:],
                "peak_tokens_per_second": self.peak_tokens_per_second,
                "min_tokens_per_second": (
                    self.min_tokens_per_second
                    if self.min_tokens_per_second != float("inf") else 0
                ),
                "total_eval_duration_ns": self.total_eval_duration_ns,
                "total_prompt_eval_duration_ns": self.total_prompt_eval_duration_ns,
                "total_load_duration_ns": self.total_load_duration_ns,
                "total_latency_ns": self.total_latency_ns,
                "model_loads_detected": self.model_loads_detected,
                "last_load_duration_ns": self.last_load_duration_ns,
            }
            await self._store.async_save(data)
            _LOGGER.debug("Saved token usage data")
        except Exception as e:
            _LOGGER.error("Failed to save token usage data: %s", e)

    def _check_daily_reset(self):
        """Reset daily stats if it's a new day"""
        today = datetime.now().date()
        if today != self.daily_reset_date:
            _LOGGER.info(
                "Daily token usage reset. Yesterday: %d input, %d output, %d requests",
                self.daily_input_tokens, self.daily_output_tokens, self.daily_requests
            )
            self.daily_input_tokens = 0
            self.daily_output_tokens = 0
            self.daily_requests = 0
            self.daily_reset_date = today

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from text length"""
        if not text:
            return 0
        return max(1, len(text) // AVG_CHARS_PER_TOKEN)

    def record_usage(
        self,
        module_id: str,
        input_tokens: int,
        output_tokens: int,
        provider: str = "ollama",
        model: str = "unknown",
        eval_duration_ns: int = 0,
        prompt_eval_duration_ns: int = 0,
        total_duration_ns: int = 0,
        load_duration_ns: int = 0,
    ) -> Dict[str, Any]:
        """Record token usage and performance metrics, return statistics"""

        self._check_daily_reset()

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_requests += 1

        self.daily_input_tokens += input_tokens
        self.daily_output_tokens += output_tokens
        self.daily_requests += 1

        tokens_per_second = 0.0
        prompt_tokens_per_second = 0.0
        latency_ms = 0.0
        time_to_first_token_ms = 0.0

        if eval_duration_ns > 0 and output_tokens > 0:
            tokens_per_second = output_tokens / (eval_duration_ns / 1e9)
            self.peak_tokens_per_second = max(self.peak_tokens_per_second, tokens_per_second)
            if self.min_tokens_per_second == float("inf") or tokens_per_second < self.min_tokens_per_second:
                self.min_tokens_per_second = tokens_per_second
            self.perf_history.append(tokens_per_second)
            if len(self.perf_history) > self.max_perf_history:
                self.perf_history = self.perf_history[-self.max_perf_history:]

        if prompt_eval_duration_ns > 0 and input_tokens > 0:
            prompt_tokens_per_second = input_tokens / (prompt_eval_duration_ns / 1e9)

        if total_duration_ns > 0:
            latency_ms = total_duration_ns / 1e6

        if load_duration_ns > 0 or prompt_eval_duration_ns > 0:
            time_to_first_token_ms = (load_duration_ns + prompt_eval_duration_ns) / 1e6

        if load_duration_ns > 50_000_000:
            self.model_loads_detected += 1
            self.last_load_duration_ns = load_duration_ns

        self.total_eval_duration_ns += eval_duration_ns
        self.total_prompt_eval_duration_ns += prompt_eval_duration_ns
        self.total_load_duration_ns += load_duration_ns
        self.total_latency_ns += total_duration_ns

        stats = self.module_stats[module_id]
        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["requests"] += 1
        stats["last_request"] = datetime.now().isoformat()
        if tokens_per_second > 0:
            prev_avg = stats.get("avg_tokens_per_second", 0.0)
            prev_n = stats["requests"] - 1
            stats["avg_tokens_per_second"] = (
                (prev_avg * prev_n + tokens_per_second) / stats["requests"]
            )

        request_entry = {
            "timestamp": datetime.now().isoformat(),
            "module_id": module_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "provider": provider,
            "model": model,
            "estimated_cost": self.calculate_cost(input_tokens, output_tokens),
            "tokens_per_second": round(tokens_per_second, 2),
            "prompt_tokens_per_second": round(prompt_tokens_per_second, 2),
            "latency_ms": round(latency_ms, 1),
            "time_to_first_token_ms": round(time_to_first_token_ms, 1),
            "load_duration_ms": round(load_duration_ns / 1e6, 1) if load_duration_ns else 0,
        }

        self.request_history.insert(0, request_entry)
        if len(self.request_history) > self.max_history:
            self.request_history = self.request_history[:self.max_history]

        asyncio.create_task(self.async_save())

        return self.get_statistics(module_id)

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost based on token counts"""
        input_cost = (input_tokens / 1000) * self.cost_per_1k_input_tokens
        output_cost = (output_tokens / 1000) * self.cost_per_1k_output_tokens
        return round(input_cost + output_cost, 6)

    def get_statistics(self, module_id: Optional[str] = None) -> Dict[str, Any]:
        """Get usage statistics, optionally filtered by module"""

        if module_id:
            stats = self.module_stats[module_id]
            total_tokens = stats["input_tokens"] + stats["output_tokens"]
            return {
                "module_id": module_id,
                "input_tokens": stats["input_tokens"],
                "output_tokens": stats["output_tokens"],
                "total_tokens": total_tokens,
                "requests": stats["requests"],
                "last_request": stats["last_request"],
                "estimated_cost": self.calculate_cost(
                    stats["input_tokens"], stats["output_tokens"]
                ),
                "avg_tokens_per_request": (
                    total_tokens / stats["requests"] if stats["requests"] > 0 else 0
                )
            }
        else:
            total_tokens = self.total_input_tokens + self.total_output_tokens
            daily_total_tokens = self.daily_input_tokens + self.daily_output_tokens

            top_modules = sorted(
                [
                    {
                        "module_id": mid,
                        "total_tokens": stats["input_tokens"] + stats["output_tokens"],
                        "requests": stats["requests"]
                    }
                    for mid, stats in self.module_stats.items()
                ],
                key=lambda x: x["total_tokens"],
                reverse=True
            )[:5]

            return {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_tokens": total_tokens,
                "total_requests": self.total_requests,
                "daily_input_tokens": self.daily_input_tokens,
                "daily_output_tokens": self.daily_output_tokens,
                "daily_total_tokens": daily_total_tokens,
                "daily_requests": self.daily_requests,
                "estimated_total_cost": self.calculate_cost(
                    self.total_input_tokens, self.total_output_tokens
                ),
                "estimated_daily_cost": self.calculate_cost(
                    self.daily_input_tokens, self.daily_output_tokens
                ),
                "avg_tokens_per_request": (
                    total_tokens / self.total_requests if self.total_requests > 0 else 0
                ),
                "top_modules": top_modules,
                "cost_per_1k_input": self.cost_per_1k_input_tokens,
                "cost_per_1k_output": self.cost_per_1k_output_tokens,
                "provider_configured": (
                    self.cost_per_1k_input_tokens > 0
                    or self.cost_per_1k_output_tokens > 0
                ),
                "avg_tokens_per_second": (
                    round(sum(self.perf_history) / len(self.perf_history), 2)
                    if self.perf_history else 0.0
                ),
                "last_tokens_per_second": (
                    round(self.perf_history[-1], 2) if self.perf_history else 0.0
                ),
                "peak_tokens_per_second": round(self.peak_tokens_per_second, 2),
                "min_tokens_per_second": (
                    round(self.min_tokens_per_second, 2)
                    if self.min_tokens_per_second != float("inf") else 0.0
                ),
                "avg_latency_ms": (
                    round((self.total_latency_ns / self.total_requests) / 1e6, 1)
                    if self.total_requests > 0 and self.total_latency_ns > 0 else 0.0
                ),
                "avg_time_to_first_token_ms": (
                    round(
                        ((self.total_load_duration_ns + self.total_prompt_eval_duration_ns)
                         / self.total_requests) / 1e6, 1
                    )
                    if self.total_requests > 0 else 0.0
                ),
                "avg_prompt_tokens_per_second": (
                    round(
                        self.total_input_tokens / (self.total_prompt_eval_duration_ns / 1e9), 2
                    )
                    if self.total_prompt_eval_duration_ns > 0 else 0.0
                ),
                "model_loads_detected": self.model_loads_detected,
                "last_load_duration_ms": (
                    round(self.last_load_duration_ns / 1e6, 1)
                    if self.last_load_duration_ns > 0 else 0.0
                ),
                "perf_sample_count": len(self.perf_history),
            }

    def set_pricing(self, input_cost_per_1k: float, output_cost_per_1k: float):
        """Configure pricing for cost estimation"""
        self.cost_per_1k_input_tokens = input_cost_per_1k
        self.cost_per_1k_output_tokens = output_cost_per_1k
        _LOGGER.info(
            "Updated LLM pricing: $%.4f/1K input, $%.4f/1K output",
            input_cost_per_1k, output_cost_per_1k
        )


class SophiaEventLogger:
    """Tracks SOPHIA Core events for dashboard display"""

    def __init__(self, max_events: int = 20):
        self.max_events = max_events
        self.events: List[Dict[str, Any]] = []

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Log an event"""
        event_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "data": data
        }
        self.events.insert(0, event_entry)
        if len(self.events) > self.max_events:
            self.events = self.events[:self.max_events]

    def get_events(self) -> List[Dict[str, Any]]:
        """Get all logged events"""
        return self.events.copy()

    def clear(self):
        """Clear all events"""
        self.events = []


class SophiaLLMClient:
    """Shared LLM client for all SOPHIA modules.

    Modules call generate() with optional flags:
        use_web_search=True   -- query SearXNG and inject results into prompt
        use_rag=True          -- embed query, search Qdrant, inject results
        rag_collection        -- Qdrant collection name (required when use_rag=True)
        search_query          -- override the query text sent to search/RAG
                                 (useful when the prompt is long but the search
                                  term should be concise)

    All mechanics are hidden from the caller. The Ollama native pipeline and
    full token/performance tracking are untouched by augmentation.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        url: str,
        model: str,
        event_logger: SophiaEventLogger,
        token_tracker: TokenUsageTracker,
        searxng_url: str = DEFAULT_SEARXNG_URL,
        qdrant_url: str = DEFAULT_QDRANT_URL,
        tei_url: str = DEFAULT_TEI_URL,
    ):
        self.hass = hass
        self.url = url
        self.model = model
        self.event_logger = event_logger
        self.token_tracker = token_tracker
        self.searxng_url = searxng_url.rstrip("/")
        self.qdrant_url = qdrant_url.rstrip("/")
        self.tei_url = tei_url.rstrip("/")
        self._request_count = 0
        self.provider = self._detect_provider(url)

    def _detect_provider(self, url: str) -> str:
        """Detect LLM provider from URL"""
        url_lower = url.lower()
        if "openai" in url_lower or "api.openai.com" in url_lower:
            return "openai"
        elif "anthropic" in url_lower or "api.anthropic.com" in url_lower:
            return "anthropic"
        elif "together" in url_lower:
            return "together"
        elif "groq" in url_lower:
            return "groq"
        else:
            return "ollama"

    # ------------------------------------------------------------------
    # Web search support (SearXNG)
    # ------------------------------------------------------------------

    async def _search_web(
        self,
        query: str,
        num_results: int = DEFAULT_SEARCH_RESULTS
    ) -> List[Dict[str, Any]]:
        """Query SearXNG and return top results.

        Returns list of dicts with keys: title, url, content
        Returns empty list on any error so generate() can continue without search.
        """
        import aiohttp
        try:
            params = {
                "q": query,
                "format": "json",
                "categories": "general",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.searxng_url}/search",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning(
                            "SearXNG returned HTTP %d for query: %s", resp.status, query
                        )
                        return []
                    data = await resp.json()
                    results = data.get("results", [])[:num_results]
                    extracted = []
                    for r in results:
                        extracted.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "content": r.get("content", ""),
                        })
                    _LOGGER.debug(
                        "SearXNG returned %d results for: %s", len(extracted), query
                    )
                    return extracted
        except Exception as e:
            _LOGGER.warning("Web search failed for '%s': %s", query, e)
            return []

    def _build_search_context(self, query: str, results: List[Dict[str, Any]]) -> str:
        """Format SearXNG results as prompt context block"""
        if not results:
            return ""
        lines = [f"[WEB SEARCH RESULTS for: {query}]"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            if r["url"]:
                lines.append(f"   Source: {r['url']}")
            if r["content"]:
                lines.append(f"   {r['content'][:400]}")
        lines.append("[END WEB SEARCH RESULTS]")
        lines.append(
            "Use the above search results to inform your response where relevant. "
            "Cite sources by number when drawing from them."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # RAG support (TEI embeddings + Qdrant)
    # ------------------------------------------------------------------

    async def _embed_query(self, query: str) -> Optional[List[float]]:
        """Embed a query string using TEI (text-embeddings-inference).

        Returns the embedding vector, or None on failure.
        """
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.tei_url}/embed",
                    json={"inputs": query},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning(
                            "TEI embed returned HTTP %d for query: %s", resp.status, query
                        )
                        return None
                    data = await resp.json()
                    # TEI returns [[float, ...]] (batch of 1)
                    if isinstance(data, list) and len(data) > 0:
                        vector = data[0]
                        if isinstance(vector, list):
                            return vector
                    _LOGGER.warning("Unexpected TEI response format: %s", type(data))
                    return None
        except Exception as e:
            _LOGGER.warning("TEI embed failed for '%s': %s", query, e)
            return None

    async def _query_rag(
        self,
        query: str,
        collection: str,
        num_results: int = DEFAULT_RAG_RESULTS
    ) -> List[Dict[str, Any]]:
        """Embed query, search Qdrant collection, return top results.

        Returns list of dicts with keys: text, score, metadata
        Returns empty list on any error.
        """
        import aiohttp

        vector = await self._embed_query(query)
        if not vector:
            _LOGGER.warning(
                "RAG skipped for collection '%s': embedding failed", collection
            )
            return []

        try:
            payload = {
                "vector": vector,
                "limit": num_results,
                "with_payload": True,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.qdrant_url}/collections/{collection}/points/search",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning(
                            "Qdrant returned HTTP %d for collection '%s'",
                            resp.status, collection
                        )
                        return []
                    data = await resp.json()
                    points = data.get("result", [])
                    results = []
                    for p in points:
                        payload_data = p.get("payload", {})
                        results.append({
                            "text": payload_data.get("text", payload_data.get("content", "")),
                            "score": round(p.get("score", 0.0), 4),
                            "metadata": {
                                k: v for k, v in payload_data.items()
                                if k not in ("text", "content")
                            },
                        })
                    _LOGGER.debug(
                        "Qdrant returned %d results from '%s' for: %s",
                        len(results), collection, query
                    )
                    return results
        except Exception as e:
            _LOGGER.warning(
                "RAG query failed for collection '%s', query '%s': %s",
                collection, query, e
            )
            return []

    def _build_rag_context(
        self, query: str, collection: str, results: List[Dict[str, Any]]
    ) -> str:
        """Format Qdrant results as prompt context block"""
        if not results:
            return ""
        lines = [f"[KNOWLEDGE BASE: {collection} | query: {query}]"]
        for i, r in enumerate(results, 1):
            score_pct = int(r["score"] * 100)
            lines.append(f"{i}. (relevance {score_pct}%) {r['text'][:600]}")
        lines.append(f"[END KNOWLEDGE BASE: {collection}]")
        lines.append(
            "Use the above knowledge base context to inform your response where relevant."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Token extraction (unchanged from original)
    # ------------------------------------------------------------------

    def _extract_tokens_from_response(
        self, response_data: Dict[str, Any], prompt: str, response_text: str
    ) -> tuple:
        """Extract token counts and timing from API response (provider-specific).

        Returns (input_tokens, output_tokens, timing_dict)
        """
        timing = {
            "eval_duration_ns": 0,
            "prompt_eval_duration_ns": 0,
            "total_duration_ns": 0,
            "load_duration_ns": 0,
        }

        if self.provider == "ollama":
            input_tokens = response_data.get("prompt_eval_count", 0)
            output_tokens = response_data.get("eval_count", 0)
            timing["eval_duration_ns"] = response_data.get("eval_duration", 0)
            timing["prompt_eval_duration_ns"] = response_data.get("prompt_eval_duration", 0)
            timing["total_duration_ns"] = response_data.get("total_duration", 0)
            timing["load_duration_ns"] = response_data.get("load_duration", 0)
        elif self.provider == "openai":
            usage = response_data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
        elif self.provider == "anthropic":
            usage = response_data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
        elif self.provider in ("together", "groq"):
            usage = response_data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
        else:
            input_tokens = 0
            output_tokens = 0

        if input_tokens == 0:
            input_tokens = self.token_tracker.estimate_tokens(prompt)
        if output_tokens == 0:
            output_tokens = self.token_tracker.estimate_tokens(response_text)

        return input_tokens, output_tokens, timing

    # ------------------------------------------------------------------
    # Main generate() entry point
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        module_id: str = None,
        context: Optional[Dict] = None,
        use_web_search: bool = False,
        use_rag: bool = False,
        rag_collection: Optional[str] = None,
        search_query: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generate a response from the LLM with optional search/RAG augmentation.

        Args:
            prompt:         The full prompt to send to the LLM.
            module_id:      Calling module ID for token tracking.
            context:        Optional extra context dict (passed through to event logger).
            use_web_search: If True, query SearXNG and prepend results to prompt.
            use_rag:        If True, embed-search Qdrant and prepend results to prompt.
            rag_collection: Qdrant collection name. Required when use_rag=True.
            search_query:   Optional explicit query for search/RAG. When omitted the
                            first 300 chars of prompt are used as the search term.
        """
        import aiohttp

        augmentation_log = []

        # Determine effective search/RAG query
        effective_query = search_query or prompt[:300].strip()

        # Build augmentation context blocks
        context_blocks = []

        if use_web_search:
            _LOGGER.debug(
                "Module %s requested web search for: %s", module_id, effective_query
            )
            search_results = await self._search_web(effective_query)
            if search_results:
                block = self._build_search_context(effective_query, search_results)
                context_blocks.append(block)
                augmentation_log.append(
                    f"web_search:{len(search_results)} results"
                )
            else:
                _LOGGER.info(
                    "Web search returned no results for module %s", module_id
                )

        if use_rag:
            if not rag_collection:
                _LOGGER.warning(
                    "Module %s requested RAG but no rag_collection specified -- skipping",
                    module_id
                )
            else:
                _LOGGER.debug(
                    "Module %s requested RAG from collection '%s' for: %s",
                    module_id, rag_collection, effective_query
                )
                rag_results = await self._query_rag(effective_query, rag_collection)
                if rag_results:
                    block = self._build_rag_context(
                        effective_query, rag_collection, rag_results
                    )
                    context_blocks.append(block)
                    augmentation_log.append(
                        f"rag:{rag_collection}:{len(rag_results)} results"
                    )
                else:
                    _LOGGER.info(
                        "RAG returned no results from '%s' for module %s",
                        rag_collection, module_id
                    )

        # Assemble final prompt
        if context_blocks:
            final_prompt = "\n\n".join(context_blocks) + "\n\n" + prompt
        else:
            final_prompt = prompt

        # --- send to Ollama (native pipeline, unchanged) ---
        try:
            self._request_count += 1

            _LOGGER.debug(
                "LLM request from %s%s: %s...",
                module_id,
                " [" + ", ".join(augmentation_log) + "]" if augmentation_log else "",
                final_prompt[:100]
            )

            self.event_logger.log_event("llm_request", {
                "module_id": module_id,
                "prompt_length": len(final_prompt),
                "original_prompt_length": len(prompt),
                "augmented": bool(augmentation_log),
                "augmentation": augmentation_log,
                "context": context,
                "provider": self.provider,
            })

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": final_prompt,
                        "stream": False,
                    },
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        response_text = data.get("response", "")

                        input_tokens, output_tokens, timing = (
                            self._extract_tokens_from_response(
                                data, final_prompt, response_text
                            )
                        )

                        usage_stats = self.token_tracker.record_usage(
                            module_id=module_id or "unknown",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            provider=self.provider,
                            model=self.model,
                            eval_duration_ns=timing["eval_duration_ns"],
                            prompt_eval_duration_ns=timing["prompt_eval_duration_ns"],
                            total_duration_ns=timing["total_duration_ns"],
                            load_duration_ns=timing["load_duration_ns"],
                        )

                        tps = usage_stats.get("last_tokens_per_second", 0.0)
                        latency_ms = (
                            round(timing["total_duration_ns"] / 1e6, 1)
                            if timing["total_duration_ns"] else 0.0
                        )

                        _LOGGER.info(
                            "LLM response for %s: %d out / %d in tokens | %.1f t/s | %.0f ms%s",
                            module_id, output_tokens, input_tokens, tps, latency_ms,
                            " [" + ", ".join(augmentation_log) + "]" if augmentation_log else ""
                        )

                        self.event_logger.log_event("llm_response_success", {
                            "module_id": module_id,
                            "response_length": len(response_text),
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                            "provider": self.provider,
                            "augmentation": augmentation_log,
                        })

                        self.hass.bus.async_fire(EVENT_LLM_RESPONSE, {
                            "module_id": module_id,
                            "success": True,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        })

                        data["_token_usage"] = {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                            "provider": self.provider,
                            "estimated_cost": usage_stats.get("estimated_cost", 0.0),
                            "tokens_per_second": tps,
                            "latency_ms": latency_ms,
                            "time_to_first_token_ms": round(
                                (timing["load_duration_ns"] + timing["prompt_eval_duration_ns"])
                                / 1e6, 1
                            ) if (
                                timing["load_duration_ns"] or timing["prompt_eval_duration_ns"]
                            ) else 0.0,
                            "augmentation": augmentation_log,
                        }

                        return data
                    else:
                        _LOGGER.error("LLM request failed: HTTP %d", response.status)
                        self.event_logger.log_event("llm_request_failed", {
                            "module_id": module_id,
                            "status": response.status,
                        })
                        return None

        except Exception as e:
            _LOGGER.error("LLM error: %s", e)
            self.event_logger.log_event("llm_request_failed", {
                "module_id": module_id,
                "error": str(e),
            })
            return None

    # ------------------------------------------------------------------
    # Health check (unchanged)
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Check LLM and augmentation service health"""
        import aiohttp

        result = {
            "status": "unhealthy",
            "url": self.url,
            "model": self.model,
            "provider": self.provider,
            "available_models": [],
            "last_check": datetime.now().isoformat(),
            "error": None,
            "searxng_url": self.searxng_url,
            "searxng_status": "unchecked",
            "qdrant_url": self.qdrant_url,
            "qdrant_status": "unchecked",
            "tei_url": self.tei_url,
            "tei_status": "unchecked",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        result["status"] = "healthy"
                        result["available_models"] = [
                            m["name"] for m in data.get("models", [])
                        ]
                    else:
                        result["error"] = f"HTTP {response.status}"
        except Exception as e:
            result["error"] = str(e)

        # Check SearXNG
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.searxng_url}/search?q=test&format=json",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    result["searxng_status"] = (
                        "healthy" if resp.status == 200 else f"HTTP {resp.status}"
                    )
        except Exception as e:
            result["searxng_status"] = f"error: {e}"

        # Check Qdrant
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.qdrant_url}/collections",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    result["qdrant_status"] = (
                        "healthy" if resp.status == 200 else f"HTTP {resp.status}"
                    )
        except Exception as e:
            result["qdrant_status"] = f"error: {e}"

        # Check TEI
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.tei_url}/embed",
                    json={"inputs": "health check"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    result["tei_status"] = (
                        "healthy" if resp.status == 200 else f"HTTP {resp.status}"
                    )
        except Exception as e:
            result["tei_status"] = f"error: {e}"

        return result


class SophiaDashboardManager:
    """Manages the SOPHIA dashboard"""

    def __init__(self, hass: HomeAssistant, registry: "SophiaModuleRegistry"):
        self.hass = hass
        self.registry = registry
        self._store = Store(
            hass, version=1, minor_version=1, key=f"lovelace.{SOPHIA_DASHBOARD_URL}"
        )

    def _write_yaml_file(self, filepath: str, content: str):
        """Write YAML file synchronously (called from executor)"""
        with open(filepath, "w") as f:
            f.write(content)

    async def create_dashboard(self):
        """Create SOPHIA dashboard"""
        try:
            config = self._build_dashboard_config()
            await self._store.async_save(config)

            import yaml
            yaml_str = yaml.dump(
                config["config"], default_flow_style=False,
                allow_unicode=True, sort_keys=False
            )
            dashboard_file = self.hass.config.path("sophia_dashboard.yaml")
            await self.hass.async_add_executor_job(
                self._write_yaml_file, dashboard_file, yaml_str
            )
            _LOGGER.info("SOPHIA dashboard YAML saved to: %s", dashboard_file)

            await self.hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": "SOPHIA Dashboard Ready",
                    "message": (
                        "**Manual Setup Required (2 minutes):**\n\n"
                        "1. Settings > Dashboards > + Add Dashboard\n"
                        "2. Title: **SOPHIA**, Icon: **mdi:robot**\n"
                        "3. Click edit > Raw configuration editor\n"
                        "4. Copy content from `/config/sophia_dashboard.yaml`\n"
                        "5. Paste and Save\n\n"
                        "Or run `sophia_core.get_dashboard_yaml` for the YAML."
                    ),
                    "notification_id": "sophia_dashboard_created",
                }
            )

            self.hass.bus.async_fire(EVENT_DASHBOARD_UPDATED, {
                "action": "created",
                "file": dashboard_file,
                "timestamp": datetime.now().isoformat(),
            })
            return True
        except Exception as e:
            _LOGGER.error("Failed to create dashboard files: %s", e)
            return False

    async def update_dashboard(self):
        """Update dashboard with current modules"""
        try:
            config = self._build_dashboard_config()
            await self._store.async_save(config)

            import yaml
            yaml_str = yaml.dump(
                config["config"], default_flow_style=False,
                allow_unicode=True, sort_keys=False
            )
            dashboard_file = self.hass.config.path("sophia_dashboard.yaml")
            await self.hass.async_add_executor_job(
                self._write_yaml_file, dashboard_file, yaml_str
            )
            _LOGGER.info("SOPHIA dashboard updated")

            self.hass.bus.async_fire(EVENT_DASHBOARD_UPDATED, {
                "action": "updated",
                "timestamp": datetime.now().isoformat(),
            })
            return True
        except Exception as e:
            _LOGGER.error("Failed to update dashboard: %s", e)
            return False

    def _build_dashboard_config(self) -> Dict[str, Any]:
        """Build complete dashboard configuration from registered modules"""
        views = [self._build_overview_view()]

        for module_id, module_data in self.registry.get_all_modules().items():
            dashboard_config = module_data.get("dashboard_config")
            if dashboard_config:
                view = self._build_module_view(module_id, module_data, dashboard_config)
                if view:
                    views.append(view)
                    _LOGGER.info("Added dashboard view from module: %s", module_id)

        return {"config": {"title": "SOPHIA", "views": views}}

    def _build_overview_view(self) -> Dict[str, Any]:
        """Build the overview/about view"""
        return {
            "title": "About SOPHIA",
            "path": "about",
            "badges": [],
            "cards": [
                {
                    "type": "markdown",
                    "content": (
                        "# S.O.P.H.I.A.\n\n"
                        "**S** - Smart\n"
                        "**O** - Operations\n"
                        "**P** - Power\n"
                        "**H** - Home\n"
                        "**I** - Intelligence\n"
                        "**A** - Automation\n\n"
                        "Smart Operations for Power and Home using Intelligence and Automation\n\n"
                        "SOPHIA is an intelligent system that manages smart home operations - "
                        "controlling power consumption, climate, energy efficiency, and automating "
                        "systems throughout your home using artificial intelligence gathered "
                        "from your home automation sensors and devices.\n"
                    )
                },
                {
                    "type": "entities",
                    "title": "SOPHIA Core Status",
                    "show_header_toggle": False,
                    "entities": [
                        {"entity": "sensor.sophia_core_status", "name": "Core Status"},
                        {"entity": "sensor.sophia_llm_status", "name": "LLM Status"},
                        {"entity": "sensor.sophia_module_count", "name": "Registered Modules"},
                        {"entity": "sensor.sophia_token_usage", "name": "Token Usage"},
                        {"entity": "sensor.sophia_llm_performance", "name": "Generation Speed"},
                        {"entity": "sensor.sophia_event_log", "name": "Event Log"},
                    ]
                },
                {
                    "type": "markdown",
                    "title": "LLM Generation Performance",
                    "content": (
                        "**Generation Speed**  \n"
                        "Avg: {{ state_attr('sensor.sophia_llm_performance', 'avg_tokens_per_second') | default(0) | float | round(1) }} t/s  \n"
                        "Last: {{ state_attr('sensor.sophia_llm_performance', 'last_tokens_per_second') | default(0) | float | round(1) }} t/s  \n"
                        "Peak: {{ state_attr('sensor.sophia_llm_performance', 'peak_tokens_per_second') | default(0) | float | round(1) }} t/s  \n\n"
                        "**Latency**  \n"
                        "Avg total: {{ state_attr('sensor.sophia_llm_performance', 'avg_latency_ms') | default(0) | float | round(0) }} ms  \n"
                        "Avg TTFT: {{ state_attr('sensor.sophia_llm_performance', 'avg_time_to_first_token_ms') | default(0) | float | round(0) }} ms  \n\n"
                        "**Model Loads**  \n"
                        "Cold loads detected: {{ state_attr('sensor.sophia_llm_performance', 'model_loads_detected') | default(0) }}  \n"
                        "Samples collected: {{ state_attr('sensor.sophia_llm_performance', 'perf_sample_count') | default(0) }}"
                    )
                },
                {
                    "type": "markdown",
                    "title": "Augmentation Services",
                    "content": (
                        "**Web Search (SearXNG)**  \n"
                        "{{ state_attr('sensor.sophia_llm_status', 'searxng_status') | default('unchecked') }}  \n"
                        "{{ state_attr('sensor.sophia_llm_status', 'searxng_url') | default('') }}  \n\n"
                        "**Vector Database (Qdrant)**  \n"
                        "{{ state_attr('sensor.sophia_llm_status', 'qdrant_status') | default('unchecked') }}  \n"
                        "{{ state_attr('sensor.sophia_llm_status', 'qdrant_url') | default('') }}  \n\n"
                        "**Embedding Service (TEI)**  \n"
                        "{{ state_attr('sensor.sophia_llm_status', 'tei_status') | default('unchecked') }}  \n"
                        "{{ state_attr('sensor.sophia_llm_status', 'tei_url') | default('') }}"
                    )
                },
                {
                    "type": "markdown",
                    "title": "LLM Token Usage",
                    "content": (
                        "**Total:** {{ state_attr('sensor.sophia_token_usage', 'total_tokens') | default(0) | int }} tokens  \n"
                        "**Requests:** {{ state_attr('sensor.sophia_token_usage', 'total_requests') | default(0) | int }}  \n\n"
                        "**Today:**  \n"
                        "Tokens: {{ state_attr('sensor.sophia_token_usage', 'daily_total_tokens') | default(0) | int }}  \n"
                        "Requests: {{ state_attr('sensor.sophia_token_usage', 'daily_requests') | default(0) | int }}"
                    )
                },
                {
                    "type": "markdown",
                    "title": "Recent SOPHIA Events",
                    "content": "{{ state_attr('sensor.sophia_event_log', 'events_formatted') }}"
                },
                {
                    "type": "markdown",
                    "title": "System Information",
                    "content": (
                        "**Platform Version:** {{ state_attr('sensor.sophia_core_status', 'version') }}  \n"
                        "**Uptime:** {{ state_attr('sensor.sophia_core_status', 'uptime') }}  \n"
                        "**LLM Model:** {{ state_attr('sensor.sophia_llm_status', 'model') }}  \n"
                        "**LLM Server:** {{ state_attr('sensor.sophia_llm_status', 'url') }}  \n"
                        "**Active Modules:** {{ state_attr('sensor.sophia_module_count', 'active_modules') }}"
                    )
                },
                {
                    "type": "horizontal-stack",
                    "title": "Quick Actions",
                    "cards": [
                        {
                            "type": "button",
                            "name": "List Modules",
                            "icon": "mdi:format-list-bulleted",
                            "tap_action": {
                                "action": "call-service",
                                "service": "sophia_core.list_modules"
                            }
                        },
                        {
                            "type": "button",
                            "name": "LLM Health",
                            "icon": "mdi:heart-pulse",
                            "tap_action": {
                                "action": "call-service",
                                "service": "sophia_core.llm_health_check"
                            }
                        },
                        {
                            "type": "button",
                            "name": "Token Stats",
                            "icon": "mdi:chart-line",
                            "tap_action": {
                                "action": "call-service",
                                "service": "sophia_core.get_token_stats"
                            }
                        },
                        {
                            "type": "button",
                            "name": "Rebuild Dashboard",
                            "icon": "mdi:refresh",
                            "tap_action": {
                                "action": "call-service",
                                "service": "sophia_core.rebuild_dashboard"
                            }
                        }
                    ]
                }
            ]
        }

    def _build_module_view(
        self, module_id: str, module_data: Dict, dashboard_config: Dict
    ) -> Optional[Dict[str, Any]]:
        """Build a view from module's dashboard configuration"""
        try:
            return {
                "title": dashboard_config.get("title", module_data.get("name", module_id)),
                "path": dashboard_config.get("path", module_id.replace("sophia_", "")),
                "badges": dashboard_config.get("badges", []),
                "cards": dashboard_config.get("cards", []),
            }
        except Exception as e:
            _LOGGER.error("Failed to build view for %s: %s", module_id, e)
            return None


class SophiaModuleRegistry:
    """Registry for SOPHIA modules and their capabilities"""

    def __init__(self, hass: HomeAssistant, event_logger: SophiaEventLogger):
        self.hass = hass
        self.event_logger = event_logger
        self.modules: Dict[str, Dict[str, Any]] = {}
        self._listeners: Set[Callable] = set()

    def register_module(self, module_id: str, capabilities: Dict[str, Any]) -> bool:
        """Register a SOPHIA module and auto-expose its entities"""
        if module_id in self.modules:
            _LOGGER.warning("Module %s already registered, updating...", module_id)

        self.modules[module_id] = {
            **capabilities,
            "registered_at": datetime.now().isoformat(),
            "status": "active",
        }
        _LOGGER.info("Registered module: %s", module_id)

        entities_to_expose = []
        for key in ("sensors", "controls"):
            for entity in capabilities.get(key, []):
                if isinstance(entity, str):
                    entities_to_expose.append(entity)

        if entities_to_expose:
            try:
                from homeassistant.helpers import entity_registry as er
                ent_reg = er.async_get(self.hass)
                exposed_count = 0
                for entity_id in entities_to_expose:
                    entry = ent_reg.async_get(entity_id)
                    if entry:
                        ent_reg.async_update_entity_options(
                            entity_id, "conversation", {"should_expose": True}
                        )
                        exposed_count += 1
                if exposed_count > 0:
                    _LOGGER.info(
                        "Auto-exposed %d entities from %s", exposed_count, module_id
                    )
            except Exception as e:
                _LOGGER.warning("Could not auto-expose entities from %s: %s", module_id, e)

        self.event_logger.log_event("module_registered", {
            "module_id": module_id,
            "name": capabilities.get("name", module_id),
        })

        self.hass.bus.async_fire(EVENT_MODULE_REGISTERED, {
            "module_id": module_id,
            "capabilities": capabilities,
        })

        for listener in self._listeners:
            listener(module_id, "registered", capabilities)

        return True

    def unregister_module(self, module_id: str) -> bool:
        """Unregister a module"""
        if module_id not in self.modules:
            _LOGGER.warning("Cannot unregister unknown module: %s", module_id)
            return False

        module_data = self.modules.pop(module_id)
        _LOGGER.info("Unregistered module: %s", module_id)

        self.event_logger.log_event("module_unregistered", {
            "module_id": module_id,
            "name": module_data.get("name", module_id),
        })

        self.hass.bus.async_fire(EVENT_MODULE_UNREGISTERED, {"module_id": module_id})

        for listener in self._listeners:
            listener(module_id, "unregistered", module_data)

        return True

    def get_module(self, module_id: str) -> Optional[Dict[str, Any]]:
        """Get a module's data"""
        return self.modules.get(module_id)

    def get_all_modules(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered modules"""
        return self.modules.copy()

    def update_module_status(
        self, module_id: str, status: str, metadata: Optional[Dict[str, Any]] = None
    ):
        """Update a module's status"""
        if module_id not in self.modules:
            _LOGGER.warning("Cannot update unknown module: %s", module_id)
            return False

        self.modules[module_id]["status"] = status
        self.modules[module_id]["last_update"] = datetime.now().isoformat()

        if metadata:
            self.modules[module_id].setdefault("metadata", {}).update(metadata)

        self.hass.bus.async_fire(EVENT_MODULE_STATUS_CHANGE, {
            "module_id": module_id,
            "status": status,
            "metadata": metadata,
        })
        return True

    def add_listener(self, cb: Callable):
        """Add a listener for module events"""
        self._listeners.add(cb)

    def remove_listener(self, cb: Callable):
        """Remove a listener"""
        self._listeners.discard(cb)


# ----------------------------------------------------------------------
# HA setup
# ----------------------------------------------------------------------

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate SOPHIA Core config entry to a newer version"""
    _LOGGER.info(
        "Migrating SOPHIA Core config entry from version %d to %d",
        config_entry.version, 2
    )

    if config_entry.version == 1:
        # Version 1 -> 2: add augmentation service URLs with defaults
        new_data = {**config_entry.data}
        new_data.setdefault("searxng_url", DEFAULT_SEARXNG_URL)
        new_data.setdefault("qdrant_url", DEFAULT_QDRANT_URL)
        new_data.setdefault("tei_url", DEFAULT_TEI_URL)

        hass.config_entries.async_update_entry(
            config_entry, data=new_data, version=2
        )
        _LOGGER.info("Migrated SOPHIA Core config entry to version 2")
        return True

    _LOGGER.error(
        "Cannot migrate SOPHIA Core config entry from version %d", config_entry.version
    )
    return False


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the SOPHIA Core component"""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SOPHIA Core from a config entry"""

    _LOGGER.info("Setting up SOPHIA Core v1.5.0...")

    mistral_url = entry.data.get("ollama_url", "http://localhost:11434")
    mistral_model = entry.data.get("ollama_model", "mistral:latest")
    searxng_url = entry.data.get("searxng_url", DEFAULT_SEARXNG_URL)
    qdrant_url = entry.data.get("qdrant_url", DEFAULT_QDRANT_URL)
    tei_url = entry.data.get("tei_url", DEFAULT_TEI_URL)

    event_logger = SophiaEventLogger()
    token_tracker = TokenUsageTracker(hass)
    await token_tracker.async_load()

    registry = SophiaModuleRegistry(hass, event_logger)

    llm_client = SophiaLLMClient(
        hass=hass,
        url=mistral_url,
        model=mistral_model,
        event_logger=event_logger,
        token_tracker=token_tracker,
        searxng_url=searxng_url,
        qdrant_url=qdrant_url,
        tei_url=tei_url,
    )

    dashboard_manager = SophiaDashboardManager(hass, registry)

    hass.data[DOMAIN] = {
        "registry": registry,
        "llm_client": llm_client,
        "dashboard_manager": dashboard_manager,
        "event_logger": event_logger,
        "token_tracker": token_tracker,
        "config": entry.data,
        "startup_time": datetime.now(),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def delayed_dashboard_create(now):
        await dashboard_manager.create_dashboard()

    from homeassistant.helpers.event import async_call_later
    async_call_later(hass, 10, delayed_dashboard_create)

    @callback
    def handle_module_change(event: Event):
        asyncio.create_task(dashboard_manager.update_dashboard())

    hass.bus.async_listen(EVENT_MODULE_REGISTERED, handle_module_change)
    hass.bus.async_listen(EVENT_MODULE_UNREGISTERED, handle_module_change)

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    async def handle_list_modules(call):
        modules = registry.get_all_modules()
        message = "**Registered SOPHIA Modules:**\n\n"
        for module_id, data in modules.items():
            icon = "+" if data.get("status") == "active" else "-"
            message += f"{icon} **{data.get('name', module_id)}** (v{data.get('version', '?')})\n"
            message += f"   Services: {', '.join(data.get('services', []))}\n"
            message += f"   Status: {data.get('status', 'unknown')}\n\n"
        if not modules:
            message = "No SOPHIA modules registered yet."
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Module Registry", "message": message,
             "notification_id": "sophia_module_list"}
        )

    async def handle_module_status(call):
        module_id = call.data.get("module_id")
        if not module_id:
            await hass.services.async_call(
                "persistent_notification", "create",
                {"title": "Error",
                 "message": "module_id parameter is required",
                 "notification_id": "sophia_module_status_error"}
            )
            return
        module_data = registry.get_module(module_id)
        if not module_data:
            await hass.services.async_call(
                "persistent_notification", "create",
                {"title": "Module Not Found",
                 "message": f"Module '{module_id}' not registered",
                 "notification_id": f"sophia_module_status_{module_id}"}
            )
            return
        token_stats = token_tracker.get_statistics(module_id)
        message = f"**{module_data.get('name', module_id)}**\n\n"
        message += f"Version: {module_data.get('version', 'unknown')}\n"
        message += f"Status: {module_data.get('status', 'unknown')}\n"
        message += f"Registered: {module_data.get('registered_at', 'unknown')}\n\n"
        message += f"**Services:** {', '.join(module_data.get('services', []))}\n"
        message += f"**Sensors:** {len(module_data.get('sensors', []))}\n"
        message += f"**LLM Access:** {'Yes' if module_data.get('requires_llm') else 'No'}\n\n"
        if token_stats["requests"] > 0:
            message += f"**LLM Token Usage:**\n"
            message += f"Requests: {token_stats['requests']}\n"
            message += f"Total Tokens: {token_stats['total_tokens']}\n"
            message += f"Avg per Request: {token_stats['avg_tokens_per_request']:.0f}\n"
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": f"SOPHIA Module: {module_data.get('name')}",
             "message": message,
             "notification_id": f"sophia_module_status_{module_id}"}
        )

    async def handle_rebuild_dashboard(call):
        _LOGGER.info("Rebuilding SOPHIA dashboard...")
        success = await dashboard_manager.update_dashboard()
        message = (
            "Dashboard rebuilt. Refresh your browser."
            if success else "Dashboard rebuild failed. Check logs."
        )
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Dashboard", "message": message,
             "notification_id": "sophia_dashboard_rebuild"}
        )

    async def handle_llm_health_check(call):
        _LOGGER.info("Running LLM health check...")
        health = await llm_client.health_check()
        ok = health.get("status") == "healthy"
        message = f"**LLM:** {health.get('status')}\n"
        message += f"URL: {health.get('url')}\n"
        message += f"Model: {health.get('model')}\n"
        if ok:
            message += f"Models: {', '.join(health.get('available_models', []))}\n"
        else:
            message += f"Error: {health.get('error')}\n"
        message += f"\n**Augmentation Services:**\n"
        message += f"SearXNG: {health.get('searxng_status')} ({health.get('searxng_url')})\n"
        message += f"Qdrant: {health.get('qdrant_status')} ({health.get('qdrant_url')})\n"
        message += f"TEI: {health.get('tei_status')} ({health.get('tei_url')})\n"
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA LLM Health", "message": message,
             "notification_id": "sophia_llm_health"}
        )

    async def handle_get_dashboard_yaml(call):
        import yaml
        dm = hass.data[DOMAIN]["dashboard_manager"]
        config = dm._build_dashboard_config()
        yaml_str = yaml.dump(
            config["config"], default_flow_style=False,
            allow_unicode=True, sort_keys=False
        )
        dashboard_file = hass.config.path("sophia_dashboard.yaml")
        await hass.async_add_executor_job(
            lambda: open(dashboard_file, "w").write(yaml_str)
        )
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Dashboard YAML",
             "message": f"Full YAML saved to `/config/sophia_dashboard.yaml`",
             "notification_id": "sophia_dashboard_yaml"}
        )

    async def handle_expose_all_entities(call):
        try:
            from homeassistant.helpers import entity_registry as er
            all_entities = []
            for module_data in registry.get_all_modules().values():
                for key in ("sensors", "controls"):
                    for e in module_data.get(key, []):
                        if isinstance(e, str):
                            all_entities.append(e)
            ent_reg = er.async_get(hass)
            exposed_count = 0
            for entity_id in all_entities:
                entry = ent_reg.async_get(entity_id)
                if entry:
                    ent_reg.async_update_entity_options(
                        entity_id, "conversation", {"should_expose": True}
                    )
                    exposed_count += 1
            message = f"Exposed {exposed_count} of {len(all_entities)} SOPHIA entities."
        except Exception as e:
            _LOGGER.error("Error exposing entities: %s", e)
            message = f"Error: {e}"
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Entity Exposure", "message": message,
             "notification_id": "sophia_expose_entities"}
        )

    async def handle_get_token_stats(call):
        stats = token_tracker.get_statistics()
        message = "**SOPHIA Token Usage Statistics**\n\n"
        message += f"Requests: {stats['total_requests']}\n"
        message += f"Total Tokens: {stats['total_tokens']:,}\n"
        message += f"Today: {stats['daily_total_tokens']:,} tokens, {stats['daily_requests']} requests\n\n"
        if stats["top_modules"]:
            message += "**Top Modules:**\n"
            for i, m in enumerate(stats["top_modules"][:5], 1):
                message += f"{i}. {m['module_id']}: {m['total_tokens']:,} tokens\n"
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Token Usage", "message": message,
             "notification_id": "sophia_token_stats"}
        )

    async def handle_set_token_pricing(call):
        input_cost = call.data.get("input_cost_per_1k", 0.0)
        output_cost = call.data.get("output_cost_per_1k", 0.0)
        token_tracker.set_pricing(input_cost, output_cost)
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Token Pricing",
             "message": f"Input: ${input_cost:.4f}/1K, Output: ${output_cost:.4f}/1K",
             "notification_id": "sophia_token_pricing"}
        )

    async def handle_reset_token_stats(call):
        confirm = call.data.get("confirm", "")
        if confirm != "RESET":
            await hass.services.async_call(
                "persistent_notification", "create",
                {"title": "SOPHIA Reset Aborted",
                 "message": "Type exactly **RESET** in the confirm field to proceed.",
                 "notification_id": "sophia_reset_aborted"}
            )
            return
        old_total = token_tracker.total_input_tokens + token_tracker.total_output_tokens
        old_requests = token_tracker.total_requests
        token_tracker.total_input_tokens = 0
        token_tracker.total_output_tokens = 0
        token_tracker.total_requests = 0
        token_tracker.daily_input_tokens = 0
        token_tracker.daily_output_tokens = 0
        token_tracker.daily_requests = 0
        token_tracker.daily_reset_date = datetime.now().date()
        token_tracker.module_stats = defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0,
            "requests": 0, "last_request": None, "avg_tokens_per_second": 0.0,
        })
        token_tracker.request_history = []
        token_tracker.perf_history = []
        token_tracker.peak_tokens_per_second = 0.0
        token_tracker.min_tokens_per_second = float("inf")
        token_tracker.total_eval_duration_ns = 0
        token_tracker.total_prompt_eval_duration_ns = 0
        token_tracker.total_load_duration_ns = 0
        token_tracker.total_latency_ns = 0
        token_tracker.model_loads_detected = 0
        token_tracker.last_load_duration_ns = 0
        await token_tracker.async_save()
        _LOGGER.warning(
            "Token stats reset. Cleared %d tokens, %d requests.", old_total, old_requests
        )
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "SOPHIA Stats Reset",
             "message": f"Cleared {old_total:,} tokens across {old_requests} requests.",
             "notification_id": "sophia_stats_reset"}
        )

    hass.services.async_register(DOMAIN, "list_modules", handle_list_modules)
    hass.services.async_register(DOMAIN, "get_module_status", handle_module_status)
    hass.services.async_register(DOMAIN, "rebuild_dashboard", handle_rebuild_dashboard)
    hass.services.async_register(DOMAIN, "llm_health_check", handle_llm_health_check)
    hass.services.async_register(DOMAIN, "get_dashboard_yaml", handle_get_dashboard_yaml)
    hass.services.async_register(DOMAIN, "expose_all_entities", handle_expose_all_entities)
    hass.services.async_register(DOMAIN, "get_token_stats", handle_get_token_stats)
    hass.services.async_register(DOMAIN, "set_token_pricing", handle_set_token_pricing)
    hass.services.async_register(DOMAIN, "reset_token_stats", handle_reset_token_stats)

    event_logger.log_event("core_started", {
        "version": "1.5.0",
        "llm_url": mistral_url,
        "llm_model": mistral_model,
        "llm_provider": llm_client.provider,
        "searxng_url": searxng_url,
        "qdrant_url": qdrant_url,
        "tei_url": tei_url,
        "token_tracking": "enabled",
        "augmentation": "enabled",
    })

    registry.register_module(DOMAIN, {
        "name": "SOPHIA Core",
        "version": "1.5.0",
        "sensors": [
            "sensor.sophia_core_status",
            "sensor.sophia_llm_status",
            "sensor.sophia_module_count",
            "sensor.sophia_token_usage",
            "sensor.sophia_llm_performance",
            "sensor.sophia_event_log",
        ],
        "controls": [],
        "services": [
            "list_modules", "get_module_status", "rebuild_dashboard",
            "llm_health_check", "get_dashboard_yaml", "expose_all_entities",
            "get_token_stats", "set_token_pricing", "reset_token_stats",
        ],
        "requires_llm": False,
        "metadata": {
            "description": (
                "Core platform providing module registry, LLM client, "
                "event bus, token tracking, web search, and RAG retrieval"
            )
        },
    })

    _LOGGER.info("SOPHIA Core v1.5.0 setup complete")
    _LOGGER.info(
        "LLM: %s (model: %s, provider: %s)", mistral_url, mistral_model, llm_client.provider
    )
    _LOGGER.info(
        "Augmentation: SearXNG=%s, Qdrant=%s, TEI=%s", searxng_url, qdrant_url, tei_url
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload SOPHIA Core"""
    _LOGGER.info("Unloading SOPHIA Core...")

    if DOMAIN in hass.data:
        token_tracker = hass.data[DOMAIN].get("token_tracker")
        if token_tracker:
            await token_tracker.async_save()

        registry = hass.data[DOMAIN].get("registry")
        if registry:
            for module_id in list(registry.modules.keys()):
                if module_id != DOMAIN:
                    registry.unregister_module(module_id)
            if DOMAIN in registry.modules:
                registry.unregister_module(DOMAIN)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload SOPHIA Core config entry"""
    _LOGGER.info("Reloading SOPHIA Core...")
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
    _LOGGER.info("SOPHIA Core reload complete")