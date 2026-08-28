#!/usr/bin/env python3
"""Patch Open-WebUI's openai.py to enrich /v1/models with workspace display names.

Open-WebUI's /api/v1/models endpoint returns the raw OpenAI model list from the
llama-server. The llama-server only exposes `id` (the alias), so the chat model
selector shows raw names like `qwen-fast`. This script patches
`/app/backend/open_webui/routers/openai.py` so the model names are looked up from
the Open-WebUI `model` table, where we already set descriptive display names.

This script is designed to run inside the Open-WebUI container via
`apply-model-config.sh` after each container restart. It is idempotent.
"""

import sys
from pathlib import Path

OPENAI_PATH = Path("/app/backend/open_webui/routers/openai.py")

PATCH_MARKER = "Enrich connection model names from Open-WebUI workspace records"

OLD_BLOCK = """    models = get_merged_models(map(extract_data, responses))
    log.debug('models: %s', models)

    request.app.state.OPENAI_MODELS = models
    return {'data': list(models.values())}"""

NEW_BLOCK = """    models = get_merged_models(map(extract_data, responses))
    log.debug('models: %s', models)

    # Enrich connection model names from Open-WebUI workspace records so the
    # chat model selector shows descriptive display names instead of raw aliases.
    try:
        from open_webui.models.models import Models as _Models

        model_records = {
            m.id: m for m in await _Models.get_models_by_ids(list(models.keys()))
        }
        for model_id, model in models.items():
            record = model_records.get(model_id)
            if record and record.name:
                model["name"] = record.name
    except Exception:
        log.exception("Failed to enrich model names from workspace records")

    request.app.state.OPENAI_MODELS = models
    return {'data': list(models.values())}"""


def main() -> int:
    if not OPENAI_PATH.exists():
        print(f"ERROR: {OPENAI_PATH} not found", file=sys.stderr)
        return 1

    text = OPENAI_PATH.read_text()

    if PATCH_MARKER in text:
        print("openai.py already patched; nothing to do.")
        return 0

    if OLD_BLOCK not in text:
        print(
            "ERROR: could not find the expected block to patch; "
            "the upstream openai.py may have changed.",
            file=sys.stderr,
        )
        return 1

    text = text.replace(OLD_BLOCK, NEW_BLOCK)
    OPENAI_PATH.write_text(text)
    print("openai.py patched to use workspace display names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
