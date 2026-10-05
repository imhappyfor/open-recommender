from open_recommender.cli import signed_owner_payload


def owner_post(client, profile, key, path, *, json=None, headers=None):
    """Send an actual owner-signed decision in existing integration tests."""
    target_id, action = path.rstrip("/").split("/")[-2:]
    if path.endswith("/events/read"):
        target_id, action = profile.profile_id, "sync-read"

    def sender(method, url, body):
        response = client.request(method, url, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    payload = signed_owner_payload(
        "http://testserver", profile.profile_id, action, target_id, json or {}, key,
        sender=sender,
    )
    return client.post(path, json=payload, headers=headers)
