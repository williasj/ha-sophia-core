# -*- coding: utf-8 -*-
"""Config flow for SOPHIA Core"""
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .__init__ import (
    DEFAULT_SEARXNG_URL,
    DEFAULT_QDRANT_URL,
    DEFAULT_TEI_URL,
)

DOMAIN = "sophia_core"

CONFIG_SCHEMA = vol.Schema({
    vol.Required("ollama_url", default="http://localhost:11434"): str,
    vol.Required("ollama_model", default="mistral:latest"): str,
    vol.Optional("searxng_url", default=DEFAULT_SEARXNG_URL): str,
    vol.Optional("qdrant_url", default=DEFAULT_QDRANT_URL): str,
    vol.Optional("tei_url", default=DEFAULT_TEI_URL): str,
})


class SophiaCoreConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SOPHIA Core"""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step"""

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors = {}

        if user_input is not None:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{user_input['ollama_url']}/api/tags",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status != 200:
                            errors["ollama_url"] = "cannot_connect"
            except Exception:
                errors["ollama_url"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(title="SOPHIA Core", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
            errors=errors,
            description_placeholders={
                "info": (
                    "Configure the Ollama LLM endpoint and optional augmentation services.\n\n"
                    "Augmentation services (SearXNG, Qdrant, TEI) are only used when a module "
                    "explicitly requests web search or RAG -- they are never called automatically."
                )
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler"""
        return SophiaCoreOptionsFlow(config_entry)


class SophiaCoreOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for SOPHIA Core"""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options"""
        import logging
        _LOGGER = logging.getLogger(__name__)

        errors = {}

        if user_input is not None:
            _LOGGER.info("SOPHIA Core config change requested")

            # Validate Ollama connection
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{user_input['ollama_url']}/api/tags",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status != 200:
                            errors["ollama_url"] = "cannot_connect"
                            _LOGGER.warning(
                                "Cannot connect to Ollama at %s", user_input["ollama_url"]
                            )
            except Exception as e:
                errors["ollama_url"] = "cannot_connect"
                _LOGGER.error("Failed to connect to Ollama: %s", e)

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={**self._config_entry.data, **user_input}
                )
                _LOGGER.info("SOPHIA Core config updated successfully")
                try:
                    _LOGGER.info("Reloading SOPHIA Core to apply new configuration...")
                    await self.hass.config_entries.async_reload(
                        self._config_entry.entry_id
                    )
                    _LOGGER.info("SOPHIA Core reloaded successfully")
                except Exception as err:
                    _LOGGER.error("Failed to reload SOPHIA Core: %s", err)

                return self.async_create_entry(title="", data={})

        current = self._config_entry.data

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    "ollama_url",
                    default=current.get("ollama_url", "http://localhost:11434")
                ): str,
                vol.Required(
                    "ollama_model",
                    default=current.get("ollama_model", "mistral:latest")
                ): str,
                vol.Optional(
                    "searxng_url",
                    default=current.get("searxng_url", DEFAULT_SEARXNG_URL)
                ): str,
                vol.Optional(
                    "qdrant_url",
                    default=current.get("qdrant_url", DEFAULT_QDRANT_URL)
                ): str,
                vol.Optional(
                    "tei_url",
                    default=current.get("tei_url", DEFAULT_TEI_URL)
                ): str,
            }),
            errors=errors,
            description_placeholders={
                "info": (
                    "SearXNG, Qdrant, and TEI are only used when a module explicitly "
                    "requests augmentation -- changing these URLs does not affect normal "
                    "LLM operation."
                )
            }
        )