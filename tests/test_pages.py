from fastapi.testclient import TestClient

from app.main import app


def _signup(client: TestClient, username: str) -> None:
    begin = client.post('/api/auth/signup/begin', json={'username': username})
    assert begin.status_code == 200

    complete = client.post(
        '/api/auth/signup/complete',
        json={
            'challenge_id': begin.json()['challenge_id'],
            'credential': {
                'id': f'cred-{username}-1',
                'response': {'attestationObject': f'pk-{username}-1'},
            },
        },
    )
    assert complete.status_code == 200


def test_signup_pages_are_available() -> None:
    with TestClient(app) as client:
        home = client.get('/', follow_redirects=False)
        assert home.status_code == 307
        assert home.headers['location'] == '/login'

        signup = client.get('/signup')
        assert signup.status_code == 200
        assert 'Signup' in signup.text
        assert 'Passkey label' not in signup.text
        assert 'href="/login"' in signup.text

        admin_signup = client.get('/admin_signup')
        assert admin_signup.status_code == 200
        assert 'Admin or Superadmin signup' in admin_signup.text
        assert 'Passkey label' not in admin_signup.text
        assert '<a href=' not in admin_signup.text

        login = client.get('/login')
        assert login.status_code == 200
        assert 'Login with passkey' in login.text
        assert 'href="/signup"' in login.text
        assert 'href="/admin_signup"' not in login.text
        assert 'href="/passkeys"' not in login.text

        passkeys = client.get('/passkeys')
        assert passkeys.status_code == 200
        assert 'Passkey management' in passkeys.text
        assert 'href="/login"' not in passkeys.text
        assert 'href="/signup"' not in passkeys.text
        assert 'href="/admin_signup"' not in passkeys.text

        admin = client.get('/admin', follow_redirects=False)
        assert admin.status_code == 303
        assert admin.headers['location'] == '/login'

        me = client.get('/me')
        assert me.status_code == 200
        assert 'Current session' in me.text
        assert 'href="/login"' not in me.text
        assert 'href="/signup"' not in me.text
        assert 'href="/admin_signup"' not in me.text


def test_logged_in_user_redirected_from_auth_entry_pages() -> None:
    with TestClient(app) as client:
        _signup(client, 'alice')

        login = client.get('/login', follow_redirects=False)
        assert login.status_code == 303
        assert login.headers['location'] == '/me'

        signup = client.get('/signup', follow_redirects=False)
        assert signup.status_code == 303
        assert signup.headers['location'] == '/me'

        admin_signup = client.get('/admin_signup', follow_redirects=False)
        assert admin_signup.status_code == 303
        assert admin_signup.headers['location'] == '/me'

        me = client.get('/me')
        assert me.status_code == 200
        assert 'href="/admin"' not in me.text
        assert 'Passkeys</a> |' not in me.text

        passkeys = client.get('/passkeys')
        assert passkeys.status_code == 200
        assert 'href="/admin"' not in passkeys.text
