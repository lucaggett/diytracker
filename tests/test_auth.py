"""Authentication & access-control integration tests."""

from datetime import datetime, timedelta

from models import db


class TestLogin:
    def test_valid_login_sets_session(self, client, make_user):
        make_user(email="u@example.com", password="secret123")
        resp = client.post(
            "/login", data={"email": "u@example.com", "password": "secret123"}
        )
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "user_id" in sess

    def test_invalid_password_does_not_authenticate(self, client, make_user):
        make_user(email="u@example.com", password="secret123")
        resp = client.post(
            "/login",
            data={"email": "u@example.com", "password": "nope"},
            follow_redirects=True,
        )
        with client.session_transaction() as sess:
            assert "user_id" not in sess
        assert b"Invalid email or password" in resp.data

    def test_unknown_user(self, client):
        resp = client.post(
            "/login",
            data={"email": "ghost@example.com", "password": "x"},
            follow_redirects=True,
        )
        with client.session_transaction() as sess:
            assert "user_id" not in sess
        assert b"Invalid email or password" in resp.data

    def test_login_redirects_to_safe_next(self, client, make_user):
        make_user(email="u@example.com", password="secret123")
        resp = client.post(
            "/login?next=/queue",
            data={"email": "u@example.com", "password": "secret123"},
        )
        assert resp.headers["Location"] == "/queue"

    def test_login_ignores_offsite_next(self, client, make_user):
        """Open-redirect guard: an absolute URL must not be honoured."""
        make_user(email="u@example.com", password="secret123")
        resp = client.post(
            "/login?next=https://evil.example.com",
            data={"email": "u@example.com", "password": "secret123"},
        )
        assert "evil.example.com" not in resp.headers["Location"]

    def test_login_ignores_protocol_relative_next(self, client, make_user):
        """`//evil.com` starts with '/' but is a cross-origin redirect."""
        make_user(email="u@example.com", password="secret123")
        resp = client.post(
            "/login?next=//evil.example.com",
            data={"email": "u@example.com", "password": "secret123"},
        )
        assert "evil.example.com" not in resp.headers["Location"]

    def test_login_post_is_rate_limited(self, client):
        from services.limits import limiter

        limiter.enabled = True  # conftest disables it globally
        try:
            responses = [
                client.post(
                    "/login", data={"email": "x@example.com", "password": "bad"}
                )
                for _ in range(11)
            ]
            assert responses[-1].status_code == 429
        finally:
            limiter.enabled = False


class TestLogout:
    def test_logout_clears_session(self, client, make_user, login):
        user = make_user()
        login(user)
        client.post("/logout")
        with client.session_transaction() as sess:
            assert "user_id" not in sess

    def test_logout_rejects_get(self, client, make_user, login):
        user = make_user()
        login(user)
        assert client.get("/logout").status_code == 405
        with client.session_transaction() as sess:
            assert "user_id" in sess


class TestSetPassword:
    def test_valid_token_sets_password_and_logs_in(self, client, make_user):
        user = make_user(email="invitee@example.com", password=None)
        token = user.generate_invite_token()
        db.session.commit()

        resp = client.post(
            f"/set-password/{token}",
            data={"password": "brandnew1", "confirm_password": "brandnew1"},
        )
        assert resp.status_code == 302
        db.session.refresh(user)
        assert user.check_password("brandnew1")
        assert user.invite_token is None  # consumed
        with client.session_transaction() as sess:
            assert sess["user_id"] == user.id

    def test_expired_token_rejected(self, client, make_user):
        user = make_user(email="invitee@example.com", password=None)
        user.generate_invite_token()
        user.invite_token_expiry = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
        resp = client.get(f"/set-password/{user.invite_token}", follow_redirects=True)
        assert b"invalid or has expired" in resp.data

    def test_unknown_token_rejected(self, client):
        resp = client.get("/set-password/does-not-exist", follow_redirects=True)
        assert b"invalid or has expired" in resp.data


class TestAccessControl:
    def test_login_required_redirects_anonymous(self, client):
        resp = client.get("/submit")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert (
            "next=%2Fsubmit" in resp.headers["Location"]
            or "next=/submit" in resp.headers["Location"]
        )

    def test_admin_route_redirects_anonymous(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_admin_route_forbidden_for_non_admin(self, client, make_user, login):
        login(make_user(is_admin=False))
        resp = client.get("/admin")
        assert resp.status_code == 403

    def test_admin_route_ok_for_admin(self, client, admin, login):
        login(admin)
        resp = client.get("/admin")
        assert resp.status_code == 200

    def test_stale_session_for_deleted_user_redirects(self, client, make_user, login):
        user = make_user()
        login(user)
        db.session.delete(user)
        db.session.commit()
        resp = client.get("/submit")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
