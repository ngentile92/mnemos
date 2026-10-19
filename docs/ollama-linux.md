# Ollama on a Linux host: container, AMD iGPU, context and output caps

Notes for Linux servers that run Ollama next to Mnemos. On macOS, keep the Ollama app (FULL-SETUP §3).

## 1. Ollama as a container on the compose network

Instead of exposing a host Ollama on the `docker0` bridge (`OLLAMA_HOST=0.0.0.0` + a firewall rule), run Ollama as
a container and join it to Mnemos' compose network. Port 11434 is never published, so it stays off the LAN, and
Cognee and the gateways reach it by name (`http://ollama:11434`).

```yaml
# e.g. /srv/docker/ollama/compose.yaml (its own stack)
name: ollama
services:
  ollama:
    image: ollama/ollama:<pinned version>
    container_name: ollama
    restart: unless-stopped
    mem_limit: 7g
    environment:
      OLLAMA_NUM_PARALLEL: "1"
      OLLAMA_KEEP_ALIVE: 5m
    volumes: ["models:/root/.ollama"]
    networks: [mnemos]
volumes:
  models: {}
networks:
  mnemos:
    external: true
    name: mnemos_internal   # <COMPOSE_PROJECT_NAME>_internal
```

Then in `cognee.env`: `LLM_ENDPOINT=http://ollama:11434/v1`, and in `.env`: `HUB_EMBED_URL=http://ollama:11434`
(`HUB_LLM_URL` defaults to it).

**Join Ollama to the compose network; do not add a second network to the `ts-<ctx>` sidecars.** The gateways share
their sidecar's network namespace, and the gateway's internal listener (`:8100`, used by the router and the
dashboard) binds to the container's first address. With a second network on the sidecar, that address may be on the
wrong network and the listener stops being reachable from the compose network.

## 2. AMD integrated GPUs (Vulkan)

Ollama runs on AMD iGPUs through Vulkan. Add to the service above:

```yaml
    environment:
      OLLAMA_VULKAN: "1"
      OLLAMA_IGPU_ENABLE: "1"
    devices: ["/dev/dri:/dev/dri"]
    group_add: ["<render gid>", "<video gid>"]   # getent group render video
```

Check with `docker logs ollama | grep -i vulkan` once a model is loaded (`Vulkan0` buffers show up) and
`docker exec ollama ollama ps` (`PROCESSOR` shows the GPU share).

Measured on a Ryzen 7 4700U (Vega 7) with `llama3.1:8b`: prompt 22 → 46 tok/s, generation 3.7 → 5.9 tok/s, and
about 2 CPU threads busy instead of all of them.

The model memory is allocated by the GPU driver from shared RAM, **outside** the container's `mem_limit`: budget
host RAM for it.

## 3. Context and output caps (Modelfile)

With `LLM_PROVIDER=ollama`, Cognee sends `num_ctx` in `extra_body` to Ollama's OpenAI-compatible `/v1` endpoint,
but Ollama may still load the model with its default context (check the `CONTEXT` column of `ollama ps`). Cognee
also uses `OLLAMA_NUM_CTX` as `max_completion_tokens`, so an 8B model that starts looping during graph extraction
can generate thousands of tokens for a short note and stall ingestion for that dataset.

Fix both with a Modelfile variant and point `LLM_MODEL` at it:

```
FROM llama3.1:8b
PARAMETER num_ctx 8192
PARAMETER num_predict 1536
PARAMETER num_thread 2
```

```bash
docker exec -i ollama ollama create llama3.1-capped -f - < Modelfile
# cognee.env
LLM_MODEL=llama3.1-capped
OLLAMA_NUM_CTX=8192
```

`num_predict` caps each answer (1536 is plenty for one note's graph extraction); `num_thread` keeps the CPU free
when the GPU does the work. Restart Cognee after changing `cognee.env`.
