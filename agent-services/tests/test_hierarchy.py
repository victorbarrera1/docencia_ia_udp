def test_hierarchy_requires_service_key(client):
    assert client.get("/api/v1/tree/hierarchy").status_code == 401


def test_hierarchy_rejects_wrong_key(client):
    res = client.get("/api/v1/tree/hierarchy", headers={"X-Agent-Service-Key": "otra"})
    assert res.status_code == 401


def test_hierarchy_returns_tree(client, auth_headers):
    res = client.get("/api/v1/tree/hierarchy", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["tree"]
    assert body["total_nodes"] == len(body["tree"])
    ids = {node["id"] for node in body["tree"]}
    assert {"institution:udp", "course:cit3203"} <= ids
