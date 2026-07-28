import pytest

@pytest.mark.asyncio
async def test_login_invalid(client):
    resp = await client.post('/api/auth/login', json={
        'username': 'nonexistent_user_xyz',
        'password': 'wrong',
    })
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_api_test_endpoint(client):
    resp = await client.get('/api/test')
    assert resp.status_code == 200
    assert resp.json()['message'] == 'NovelHub API'
