def test_agent_card_is_served(client):
    res = client.get("/.well-known/agent-card.json")
    assert res.status_code == 200
    card = res.json()
    assert card["name"]
    assert card["version"]
