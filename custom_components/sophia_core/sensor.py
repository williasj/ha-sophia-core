# -*- coding: utf-8 -*-
"""SOPHIA Core sensors for health monitoring and dashboard - NOW WITH PERFORMANCE TRACKING"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sophia_core"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SOPHIA Core sensors"""
    
    registry = hass.data[DOMAIN]["registry"]
    llm_client = hass.data[DOMAIN]["llm_client"]
    event_logger = hass.data[DOMAIN]["event_logger"]
    token_tracker = hass.data[DOMAIN]["token_tracker"]
    startup_time = hass.data[DOMAIN]["startup_time"]
    
    sensors = [
        SophiaCoreStatusSensor(hass, startup_time),
        SophiaLLMStatusSensor(hass, llm_client),
        SophiaModuleCountSensor(hass, registry),
        SophiaTokenUsageSensor(hass, token_tracker),
        SophiaLLMPerformanceSensor(hass, token_tracker),  # NEW: generation speed + latency
        SophiaEventLogSensor(hass, event_logger),
    ]
    
    async_add_entities(sensors)
    
    # Schedule periodic LLM health checks (every 5 minutes)
    async def periodic_llm_check(now):
        """Periodic LLM health check"""
        for sensor in sensors:
            if isinstance(sensor, SophiaLLMStatusSensor):
                await sensor.async_update()
                sensor.async_write_ha_state()
    
    async_track_time_interval(hass, periodic_llm_check, timedelta(minutes=5))


class SophiaCoreStatusSensor(SensorEntity):
    """Sensor for SOPHIA Core status"""
    
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, hass: HomeAssistant, startup_time: datetime):
        """Initialize the sensor"""
        self.hass = hass
        self._startup_time = startup_time
        self._attr_unique_id = f"{DOMAIN}_status"
        self._attr_name = "SOPHIA Core Status"
        self._attr_icon = "mdi:robot"
    
    @property
    def state(self) -> str:
        """Return the state"""
        return "active"
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes"""
        uptime = datetime.now() - self._startup_time
        
        return {
            "version": "1.5.1",
            "startup_time": self._startup_time.isoformat(),
            "uptime": str(uptime).split('.')[0],  # Remove microseconds
            "uptime_seconds": int(uptime.total_seconds())
        }


class SophiaLLMStatusSensor(SensorEntity):
    """Sensor for LLM connection status"""
    
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, hass: HomeAssistant, llm_client):
        """Initialize the sensor"""
        self.hass = hass
        self._llm_client = llm_client
        self._attr_unique_id = f"{DOMAIN}_llm_status"
        self._attr_name = "SOPHIA LLM Status"
        self._health_data = {}
    
    @property
    def state(self) -> str:
        """Return the state"""
        return self._health_data.get("status", "unknown")
    
    @property
    def icon(self) -> str:
        """Return icon based on status"""
        status = self._health_data.get("status", "unknown")
        if status == "healthy":
            return "mdi:check-circle"
        elif status == "unhealthy":
            return "mdi:alert-circle"
        else:
            return "mdi:help-circle"
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes"""
        return {
            "url": self._health_data.get("url", ""),
            "model": self._health_data.get("model", ""),
            "provider": self._health_data.get("provider", "unknown"),
            "last_check": self._health_data.get("last_check", "never"),
            "available_models": self._health_data.get("available_models", []),
            "error": self._health_data.get("error", None),
            "searxng_url": self._health_data.get("searxng_url", ""),
            "searxng_status": self._health_data.get("searxng_status", "unchecked"),
            "qdrant_url": self._health_data.get("qdrant_url", ""),
            "qdrant_status": self._health_data.get("qdrant_status", "unchecked"),
            "tei_url": self._health_data.get("tei_url", ""),
            "tei_status": self._health_data.get("tei_status", "unchecked"),
        }
    
    async def async_update(self):
        """Update the sensor"""
        self._health_data = await self._llm_client.health_check()


class SophiaModuleCountSensor(SensorEntity):
    """Sensor for number of registered modules"""
    
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, hass: HomeAssistant, registry):
        """Initialize the sensor"""
        self.hass = hass
        self._registry = registry
        self._attr_unique_id = f"{DOMAIN}_module_count"
        self._attr_name = "SOPHIA Module Count"
        self._attr_icon = "mdi:puzzle"
        
        # Listen for module registration changes
        @callback
        def update_on_change(module_id, action, data):
            """Update when modules change"""
            self.async_schedule_update_ha_state()
        
        registry.add_listener(update_on_change)
    
    @property
    def state(self) -> int:
        """Return the state"""
        return len(self._registry.get_all_modules())
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes"""
        modules = self._registry.get_all_modules()
        
        return {
            "registered_modules": list(modules.keys()),
            "active_modules": [
                mid for mid, data in modules.items()
                if data.get("status") == "active"
            ],
            "modules_with_llm": [
                mid for mid, data in modules.items()
                if data.get("requires_llm", False)
            ]
        }


class SophiaTokenUsageSensor(SensorEntity):
    """NEW: Sensor for LLM token usage and cost tracking"""
    
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, hass: HomeAssistant, token_tracker):
        """Initialize the sensor"""
        self.hass = hass
        self._token_tracker = token_tracker
        self._attr_unique_id = f"{DOMAIN}_token_usage"
        self._attr_name = "SOPHIA Token Usage"
        self._attr_icon = "mdi:chart-line"
        self._attr_native_unit_of_measurement = "tokens"
    
    @property
    def state(self) -> int:
        """Return total tokens used"""
        stats = self._token_tracker.get_statistics()
        return stats.get("total_tokens", 0)
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return detailed token usage statistics"""
        stats = self._token_tracker.get_statistics()
        
        # Format recent request history for display
        recent_requests = self._token_tracker.request_history[:10]
        formatted_requests = []
        
        for req in recent_requests:
            try:
                dt = datetime.fromisoformat(req["timestamp"])
                time_str = dt.strftime("%H:%M:%S")
            except:
                time_str = req["timestamp"]
            
            formatted_requests.append({
                "time": time_str,
                "module": req["module_id"],
                "tokens": req["total_tokens"],
                "cost": f"${req['estimated_cost']:.6f}" if req['estimated_cost'] > 0 else "Free"
            })
        
        # Calculate cost comparison for different AI providers
        daily_input = stats.get("daily_input_tokens", 0)
        daily_output = stats.get("daily_output_tokens", 0)
        total_input = stats.get("total_input_tokens", 0)
        total_output = stats.get("total_output_tokens", 0)
        
        # AI Provider pricing (per 1M tokens) from IntuitionLabs report
        providers = [
            {"name": "Grok 4.1 Fast", "input_cost": 0.70, "output_cost": 0.70},
            {"name": "Claude Haiku 3.5", "input_cost": 1.00, "output_cost": 5.00},
            {"name": "Gemini 2.5 Pro", "input_cost": 2.50, "output_cost": 10.00},
            {"name": "Claude Sonnet 4", "input_cost": 3.00, "output_cost": 15.00},
            {"name": "GPT-3.5 Turbo", "input_cost": 0.50, "output_cost": 1.50},
            {"name": "GPT-4o", "input_cost": 2.50, "output_cost": 10.00},
            {"name": "Claude Opus 4", "input_cost": 15.00, "output_cost": 75.00}
        ]
        
        # Calculate cost for each provider - DAILY
        daily_comparisons = []
        for provider in providers:
            cost = (
                (daily_input / 1_000_000) * provider["input_cost"] +
                (daily_output / 1_000_000) * provider["output_cost"]
            )
            daily_comparisons.append({
                "provider": provider["name"],
                "cost": round(cost, 6),
                "formatted": f"${cost:.6f}"
            })
        
        # Sort by cost (cheapest first)
        daily_comparisons.sort(key=lambda x: x["cost"])
        
        # Calculate cost for each provider - LIFETIME
        lifetime_comparisons = []
        for provider in providers:
            cost = (
                (total_input / 1_000_000) * provider["input_cost"] +
                (total_output / 1_000_000) * provider["output_cost"]
            )
            lifetime_comparisons.append({
                "provider": provider["name"],
                "cost": round(cost, 6),
                "formatted": f"${cost:.6f}"
            })
        
        # Sort by cost (cheapest first)
        lifetime_comparisons.sort(key=lambda x: x["cost"])
        
        return {
            # Lifetime totals
            "total_input_tokens": stats.get("total_input_tokens", 0),
            "total_output_tokens": stats.get("total_output_tokens", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "total_requests": stats.get("total_requests", 0),
            
            # Daily totals
            "daily_input_tokens": stats.get("daily_input_tokens", 0),
            "daily_output_tokens": stats.get("daily_output_tokens", 0),
            "daily_total_tokens": stats.get("daily_total_tokens", 0),
            "daily_requests": stats.get("daily_requests", 0),
            
            # Averages
            "avg_tokens_per_request": round(stats.get("avg_tokens_per_request", 0), 1),
            
            # Cost estimation
            "estimated_total_cost": stats.get("estimated_total_cost", 0.0),
            "estimated_daily_cost": stats.get("estimated_daily_cost", 0.0),
            "cost_per_1k_input": stats.get("cost_per_1k_input", 0.0),
            "cost_per_1k_output": stats.get("cost_per_1k_output", 0.0),
            "provider_configured": stats.get("provider_configured", False),
            
            # Cost comparison with other AI providers
            "daily_cost_comparison_sorted": daily_comparisons,
            "lifetime_cost_comparison_sorted": lifetime_comparisons,
            
            # Top modules
            "top_modules": stats.get("top_modules", []),
            
            # Recent requests
            "recent_requests": formatted_requests,
            "recent_requests_count": len(recent_requests)
        }
    
    @property
    def icon(self) -> str:
        """Return icon based on usage"""
        stats = self._token_tracker.get_statistics()
        daily_tokens = stats.get("daily_total_tokens", 0)
        
        # Change icon based on daily usage
        if daily_tokens == 0:
            return "mdi:chart-line-variant"
        elif daily_tokens < 10000:
            return "mdi:chart-line"
        elif daily_tokens < 50000:
            return "mdi:chart-timeline-variant"
        else:
            return "mdi:chart-timeline-variant-shimmer"


class SophiaLLMPerformanceSensor(SensorEntity):
    """Sensor for LLM generation performance: tokens/second, latency, TTFT."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, token_tracker):
        """Initialize the sensor."""
        self.hass = hass
        self._token_tracker = token_tracker
        self._attr_unique_id = f"{DOMAIN}_llm_performance"
        self._attr_name = "SOPHIA LLM Performance"
        self._attr_native_unit_of_measurement = "t/s"

    @property
    def state(self) -> float:
        """Return the rolling average generation speed in tokens/second."""
        stats = self._token_tracker.get_statistics()
        return round(stats.get("avg_tokens_per_second", 0.0), 1)

    @property
    def icon(self) -> str:
        """Return icon based on generation speed."""
        tps = self._token_tracker.get_statistics().get("avg_tokens_per_second", 0.0)
        if tps == 0:
            return "mdi:speedometer-slow"
        elif tps < 5:
            return "mdi:speedometer-medium"
        elif tps < 20:
            return "mdi:speedometer"
        else:
            return "mdi:lightning-bolt"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return detailed LLM performance metrics."""
        stats = self._token_tracker.get_statistics()

        # Recent t/s history for dashboards / sparklines (last 20 samples)
        recent_tps = [round(t, 2) for t in self._token_tracker.perf_history[-20:]]

        # Build per-request perf summary from history
        recent_requests = []
        for req in self._token_tracker.request_history[:10]:
            try:
                dt = datetime.fromisoformat(req["timestamp"])
                time_str = dt.strftime("%H:%M:%S")
            except Exception:
                time_str = req.get("timestamp", "")

            tps = req.get("tokens_per_second", 0.0)
            lat = req.get("latency_ms", 0.0)
            ttft = req.get("time_to_first_token_ms", 0.0)
            out_tok = req.get("output_tokens", 0)
            cold_load = req.get("load_duration_ms", 0) > 50  # cold vs warm

            recent_requests.append({
                "time": time_str,
                "module": req.get("module_id", "unknown"),
                "output_tokens": out_tok,
                "tokens_per_second": tps,
                "latency_ms": lat,
                "time_to_first_token_ms": ttft,
                "cold_load": cold_load,
            })

        return {
            # Rolling averages
            "avg_tokens_per_second": stats.get("avg_tokens_per_second", 0.0),
            "last_tokens_per_second": stats.get("last_tokens_per_second", 0.0),
            "peak_tokens_per_second": stats.get("peak_tokens_per_second", 0.0),
            "min_tokens_per_second": stats.get("min_tokens_per_second", 0.0),

            # Prompt-processing speed (separate from generation)
            "avg_prompt_tokens_per_second": stats.get("avg_prompt_tokens_per_second", 0.0),

            # Latency
            "avg_latency_ms": stats.get("avg_latency_ms", 0.0),
            "avg_time_to_first_token_ms": stats.get("avg_time_to_first_token_ms", 0.0),

            # Model load / cold-start tracking
            "model_loads_detected": stats.get("model_loads_detected", 0),
            "last_load_duration_ms": stats.get("last_load_duration_ms", 0.0),

            # Sample data
            "perf_sample_count": stats.get("perf_sample_count", 0),
            "recent_tps_history": recent_tps,

            # Per-request breakdown (last 10)
            "recent_requests": recent_requests,
        }


class SophiaEventLogSensor(SensorEntity):
    """Sensor for SOPHIA Core event log"""
    
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, hass: HomeAssistant, event_logger):
        """Initialize the sensor"""
        self.hass = hass
        self._event_logger = event_logger
        self._attr_unique_id = f"{DOMAIN}_event_log"
        self._attr_name = "SOPHIA Event Log"
        self._attr_icon = "mdi:text-box-multiple"
    
    @property
    def state(self) -> int:
        """Return the number of logged events"""
        return len(self._event_logger.get_events())
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional attributes"""
        events = self._event_logger.get_events()
        
        # Format events for display
        events_formatted = self._format_events_for_display(events[:10])  # Last 10 for display
        
        return {
            "event_count": len(events),
            "events": events,  # Full event data
            "events_formatted": events_formatted,  # Markdown formatted
            "latest_event": events[0] if events else None
        }
    
    def _format_events_for_display(self, events) -> str:
        """Format events as markdown for dashboard"""
        if not events:
            return "No events logged yet."
        
        lines = []
        for event in events:
            timestamp = event.get("timestamp", "")
            event_type = event.get("type", "unknown")
            data = event.get("data", {})
            
            # Parse timestamp for readable format
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%H:%M:%S")
            except:
                time_str = timestamp
            
            # Format based on event type
            if event_type == "module_registered":
                module_name = data.get("name", data.get("module_id", "unknown"))
                lines.append(f"- **{time_str}** ? Module registered: {module_name}")
            
            elif event_type == "module_unregistered":
                module_name = data.get("name", data.get("module_id", "unknown"))
                lines.append(f"- **{time_str}** ? Module unregistered: {module_name}")
            
            elif event_type == "llm_request":
                module_id = data.get("module_id", "unknown")
                provider = data.get("provider", "")
                lines.append(f"- **{time_str}** ?? LLM request from {module_id} ({provider})")
            
            elif event_type == "llm_response_success":
                module_id = data.get("module_id", "unknown")
                tokens = data.get("total_tokens", 0)
                lines.append(f"- **{time_str}** ? LLM response to {module_id} ({tokens} tokens)")
            
            elif event_type == "llm_request_failed":
                module_id = data.get("module_id", "unknown")
                status = data.get("status", "unknown")
                lines.append(f"- **{time_str}** ? LLM failed ({status}) for {module_id}")
            
            elif event_type == "core_started":
                version = data.get("version", "unknown")
                lines.append(f"- **{time_str}** ?? SOPHIA Core started (v{version})")
            
            else:
                lines.append(f"- **{time_str}** {event_type}")
        
        return "\n".join(lines)