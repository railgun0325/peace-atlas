"""Explicitly exercise estimate/confirmation in behavior tests; no server bypass."""


def confirmed_post(client, url, **kwargs):
    payload = kwargs.get("json")
    if isinstance(payload, dict) and not payload.get("preview_token"):
        preview_url = None
        preview_body = payload
        if url == "/api/v2/jobs" and payload.get("kind") == "research":
            preview_url, preview_body = "/api/v2/research/estimate", payload.get("params")
        elif url == "/api/v2/jobs/batch" and payload.get("kind") == "research":
            preview_url = "/api/v2/research/batch-plan"
        elif url.startswith("/api/v2/research/") and url.endswith("/continue"):
            preview_url = url + "-estimate"
        if preview_url:
            preview = client.post(preview_url, json=preview_body)
            if preview.status_code == 200:
                assert preview.json().get("preview_token"), preview.text
                kwargs = {**kwargs, "json": {**payload, "preview_token": preview.json()["preview_token"]}}
    return client.post(url, **kwargs)
