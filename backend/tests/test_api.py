import os
import sys
import time

# ensure backend dir is on path so tests can import main when running from workspace root
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from main import app


def setup_module(module):
    # remove any existing sqlite db to start fresh
    try:
        os.remove('dev.db')
    except FileNotFoundError:
        pass
    # small wait to ensure file system settled
    time.sleep(0.1)
    # ensure tables are created
    from main import engine, SQLModel
    SQLModel.metadata.create_all(engine)


def test_register_login_create_project_and_task():
    client = TestClient(app)

    # register
    r = client.post('/register?username=alice&password=secret')
    assert r.status_code == 200

    # login
    r = client.post('/token', data={'username': 'alice', 'password': 'secret'})
    assert r.status_code == 200
    token = r.json().get('access_token')
    assert token
    headers = {'Authorization': f'Bearer {token}'}

    # create project
    r = client.post('/projects/', json={'name': 'TestProj', 'description': 'desc'}, headers=headers)
    assert r.status_code == 200
    proj = r.json()
    assert proj['name'] == 'TestProj'
    pid = proj['id']

    # list projects
    r = client.get('/projects/', headers=headers)
    assert r.status_code == 200
    projects = r.json()
    assert any(p['id'] == pid for p in projects)

    # add task
    r = client.post(f'/projects/{pid}/tasks/', json={'title': 'T1', 'project_id': pid}, headers=headers)
    assert r.status_code == 200
    task = r.json()
    assert task['title'] == 'T1'

    # list tasks
    r = client.get(f'/projects/{pid}/tasks/', headers=headers)
    assert r.status_code == 200
    tasks = r.json()
    assert any(t['id'] == task['id'] for t in tasks)
