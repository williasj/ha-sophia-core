<p align="center">
  <img src="images/sophia_logo.png" alt="SOPHIA" width="200"/>
</p>

<h1 align="center">SOPHIA Core</h1>
<p align="center">
  <strong>S</strong>mart <strong>O</strong>perations for <strong>P</strong>ower and <strong>H</strong>ome using <strong>I</strong>ntelligence and <strong>A</strong>utomation
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom"/></a>
  <img src="https://img.shields.io/badge/version-1.5.0-blue" alt="Version"/>
  <img src="https://img.shields.io/badge/HA-2024.4.0+-green" alt="Home Assistant"/>
  <img src="https://img.shields.io/badge/license-PolyForm%20NC-lightgrey" alt="License"/>
</p>

---

SOPHIA Core is the central platform that powers the SOPHIA ecosystem for Home Assistant. It provides a shared LLM client, module registry, token tracking, event bus, and optional augmentation services (web search and RAG retrieval) that all other SOPHIA modules build on.

If you just want an AI-powered smart home, start here.

---

## What SOPHIA Core Does

- **LLM Client** — shared Ollama connection used by all SOPHIA modules, with support for OpenAI-compatible endpoints
- **Module Registry** — tracks all installed SOPHIA modules and their status in a single dashboard
- **Token Tracking** — records every LLM call with input/output counts, cost estimation, and generation speed (t/s)
- **Performance Monitoring** — rolling averages for tokens/second, latency, TTFT, and cold-load detection
- **Web Search Augmentation** — optional SearXNG integration for real-time web context injection
- **RAG Retrieval** — optional Qdrant + TEI embeddings for knowledge base lookups
- **Auto Dashboard** — generates a SOPHIA dashboard in Home Assistant automatically on first load

---

## Requirements

**Required:**
- Home Assistant 2024.4.0 or later
- [Ollama](https://ollama.ai) running on your network with at least one model pulled

**Optional augmentation services** (only used when a module explicitly requests them):
- [Qdrant](https://qdrant.tech) — vector database for RAG retrieval
- [SearXNG](https://searxng.github.io/searxng/) — self-hosted web search
- [Text Embeddings Inference (TEI)](https://github.com/huggingface/text-embeddings-inference) — embedding service for RAG

All services are self-hosted. SOPHIA has no cloud dependencies.

---

## Installation

### Via HACS (Recommended)

1. In Home Assistant, open **HACS → Integrations**
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/williasj/ha-sophia-core` as an **Integration**
4. Search for **SOPHIA Core** and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/sophia_core` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Configuration

After installation and restart:

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **SOPHIA Core**
3. Enter your Ollama URL (default: `http://localhost:11434`) and model name
4. Optionally configure SearXNG, Qdrant, and TEI URLs
5. Click **Submit**

SOPHIA Core will validate the Ollama connection before completing setup. Augmentation services are optional and can be left at their defaults if not used.

---

## Sensors

SOPHIA Core creates the following diagnostic sensors in Home Assistant:

| Sensor | Description |
|---|---|
| `sensor.sophia_core_status` | Core platform status and uptime |
| `sensor.sophia_llm_status` | Ollama connection health and available models |
| `sensor.sophia_module_count` | Number of registered SOPHIA modules |
| `sensor.sophia_token_usage` | Cumulative token usage with cost estimation |
| `sensor.sophia_llm_performance` | Generation speed, latency, and TTFT metrics |
| `sensor.sophia_event_log` | Recent SOPHIA platform events |

---

## Services

| Service | Description |
|---|---|
| `sophia_core.list_modules` | Show all registered SOPHIA modules |
| `sophia_core.get_module_status` | Detailed status for a specific module |
| `sophia_core.llm_health_check` | Check Ollama and augmentation service health |
| `sophia_core.rebuild_dashboard` | Regenerate the SOPHIA dashboard |
| `sophia_core.get_dashboard_yaml` | Save dashboard YAML to `/config/` |
| `sophia_core.expose_all_entities` | Expose all SOPHIA entities to HA conversation agents |
| `sophia_core.get_token_stats` | Display token usage statistics |
| `sophia_core.set_token_pricing` | Configure cost per 1K tokens for estimation |
| `sophia_core.reset_token_stats` | Reset all token usage statistics |

---

## SOPHIA Ecosystem

SOPHIA Core is the foundation. Additional modules extend its capabilities:

| Module | Description |
|---|---|
| **SOPHIA Core** *(this repo)* | LLM client, module registry, token tracking |
| **SOPHIA Climate** | AI-driven HVAC optimization and efficiency scoring |
| **SOPHIA Presence** | Family presence tracking with RAG pattern storage |
| **SOPHIA Systems** | Hardware telemetry — TrueNAS, GPU, BMC sensors |

Each module is a separate HACS integration and installs independently.

---

## Architecture

SOPHIA is designed around a modular, local-first philosophy:

- All inference runs locally via Ollama — no cloud API calls
- Modules register themselves with SOPHIA Core on startup
- The shared LLM client handles all augmentation transparently
- Token usage is tracked per-module for full visibility
- Augmentation services (search, RAG) are opt-in per request

---

## License

SOPHIA Core is free for personal, non-commercial use under the [PolyForm Noncommercial License 1.0.0](LICENSE).

For commercial licensing inquiries, contact:
- Email: [Scott.J.Williams14@gmail.com](mailto:Scott.J.Williams14@gmail.com)
- GitHub: [@williasj](https://github.com/williasj)

---

## Contributing

Bug reports and pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

Please note that contributions are accepted under the same license terms. Commercial use of contributions requires a separate agreement.