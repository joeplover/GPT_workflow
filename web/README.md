# HiCode Image Studio

## Goal

Build a lightweight intelligent image generation platform under `WorkFlow/web` based on a relay API.

The current version focuses on:

- text-to-image via `POST /v1/images/generations`
- image-to-image via `POST /v1/images/edits`
- storyboard projects and shot history
- continuity mode that reuses the previous shot as image reference
- reusable character cards and scene cards per project
- screenplay text parsed into candidate shot beats
- single-page prompt console and local result storage

## Architecture

### Frontend

- Single-page static app served by FastAPI
- Two modes: `generate` and `edit`
- Project selector and project synopsis panel
- Screenplay input and candidate shot list
- Candidate shot list supports both apply-only and one-click generate
- Character card and scene card builders
- Shot prompt, continuity checkbox, model, size, image upload, optional mask upload
- Result preview, shot refill, retry, and per-project shot history

### Backend

- FastAPI service under `app.py`
- Wrap relay endpoints and normalize response handling
- Save returned images into `runtime/outputs`
- Save uploads into `runtime/uploads`
- Persist storyboard projects in `runtime/projects.json`
- Compose the final relay prompt from project synopsis + selected cards + current shot prompt

### Relay API strategy

- `generate` -> `POST /v1/images/generations`
- `edit` -> `POST /v1/images/edits`
- `continue_from_last` in generate mode internally reuses the latest shot image through the edit route
- Supports both `b64_json` and `url` result payloads
- Reads runtime config from environment variables:
  - `HICODE_API_KEY` or `OPENAI_API_KEY`
  - `HICODE_BASE_URL`
  - `HICODE_IMAGE_MODEL`
  - `HICODE_IMAGE_SIZE`
  - `HICODE_VERIFY_SSL`
  - `HICODE_USE_ENV_PROXY`

## Run

Set API key first:

```powershell
$env:HICODE_API_KEY="your_key"
```

You can also fill the token directly on the web page. Request-level token input overrides environment variables.

Start the server:

```powershell
uvicorn workflow.web.app:app --reload --port 8090
```

Open:

```text
http://127.0.0.1:8090
```

## Suggested next steps

1. Add authentication and per-user project ownership.
2. Add task queue for long-running jobs and retries.
3. Add character cards, location cards, and style preset libraries.
4. Add multi-image reference generation through a `responses`-style endpoint.
5. Add shot reordering, screenplay import, and gallery export.
