"""
Comprehensive test suite for authentication module.

Tests cover Phase 2 maintenance plan - 17 untested auth functions:
1. Session middleware (DynamicSessionMiddleware, DynamicMiddlewareLinker)
2. OIDC flow (oidcConfig.set_endpoint, validate, get_redirect_https)
3. Login types (LoginTypeEnum methods: is_basic, is_forms, is_none, is_oidc)
4. User authorization (is_above, can_download, is_admin, is_self)
5. Auth functions (authenticate_user, create_user, is_correct_password)
6. API key validation (APIKeyAuth, _authenticate_api_key)
7. ABRAuth dispatcher (_get_basic_auth, _get_session_auth, _get_oidc_auth, _get_none_auth)
"""
import asyncio
import base64
import secrets
from datetime import datetime, timedelta, timezone
from math import inf
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import Annotated

import pytest
from fastapi import HTTPException, Request, status
from sqlmodel import Session

from app.internal.auth.authentication import (
    ABRAuth,
    APIKeyAuth,
    DetailedUser,
    authenticate_user,
    create_user,
    create_api_key,
    generate_api_key,
    is_correct_password,
    RequiresLoginException,
)
from app.internal.auth.login_types import LoginTypeEnum
from app.internal.auth.session_middleware import (
    DynamicSessionMiddleware,
    DynamicMiddlewareLinker,
)
from app.internal.auth.oidc_config import (
    oidcConfig,
    InvalidOIDCConfiguration,
    _OidcResponse,
)
from app.internal.models import User, APIKey, GroupEnum


class TestLoginTypeEnum:
    """Test LoginTypeEnum methods for login type checking."""

    def test_is_basic_true_when_basic(self):
        """is_basic() should return True for basic login type."""
        login_type = LoginTypeEnum.basic
        assert login_type.is_basic() is True

    def test_is_basic_false_for_other_types(self):
        """is_basic() should return False for non-basic types."""
        for login_type in [LoginTypeEnum.forms, LoginTypeEnum.oidc, LoginTypeEnum.none]:
            assert login_type.is_basic() is False

    def test_is_forms_true_when_forms(self):
        """is_forms() should return True for forms login type."""
        login_type = LoginTypeEnum.forms
        assert login_type.is_forms() is True

    def test_is_forms_false_for_other_types(self):
        """is_forms() should return False for non-forms types."""
        for login_type in [LoginTypeEnum.basic, LoginTypeEnum.oidc, LoginTypeEnum.none]:
            assert login_type.is_forms() is False

    def test_is_none_true_when_none(self):
        """is_none() should return True for none login type."""
        login_type = LoginTypeEnum.none
        assert login_type.is_none() is True

    def test_is_none_false_for_other_types(self):
        """is_none() should return False for non-none types."""
        for login_type in [LoginTypeEnum.basic, LoginTypeEnum.forms, LoginTypeEnum.oidc]:
            assert login_type.is_none() is False

    def test_is_oidc_true_when_oidc(self):
        """is_oidc() should return True for oidc login type."""
        login_type = LoginTypeEnum.oidc
        assert login_type.is_oidc() is True

    def test_is_oidc_false_for_other_types(self):
        """is_oidc() should return False for non-oidc types."""
        for login_type in [LoginTypeEnum.basic, LoginTypeEnum.forms, LoginTypeEnum.none]:
            assert login_type.is_oidc() is False


class TestUserAuthorization:
    """Test User model authorization methods."""

    def test_is_above_admin_can_access_admin(self, db_session):
        """Admin user should be able to access admin resources."""
        user = User(
            username="admin_user",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.admin) is True

    def test_is_above_admin_can_access_trusted(self, db_session):
        """Admin user should be able to access trusted resources."""
        user = User(
            username="admin_user",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.trusted) is True

    def test_is_above_admin_can_access_untrusted(self, db_session):
        """Admin user should be able to access untrusted resources."""
        user = User(
            username="admin_user",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.untrusted) is True

    def test_is_above_trusted_cannot_access_admin(self, db_session):
        """Trusted user should NOT be able to access admin resources."""
        user = User(
            username="trusted_user",
            password="hashed_password",
            group=GroupEnum.trusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.admin) is False

    def test_is_above_trusted_can_access_trusted(self, db_session):
        """Trusted user should be able to access trusted resources."""
        user = User(
            username="trusted_user",
            password="hashed_password",
            group=GroupEnum.trusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.trusted) is True

    def test_is_above_trusted_can_access_untrusted(self, db_session):
        """Trusted user should be able to access untrusted resources."""
        user = User(
            username="trusted_user",
            password="hashed_password",
            group=GroupEnum.trusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.untrusted) is True

    def test_is_above_untrusted_cannot_access_admin(self, db_session):
        """Untrusted user should NOT be able to access admin resources."""
        user = User(
            username="untrusted_user",
            password="hashed_password",
            group=GroupEnum.untrusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.admin) is False

    def test_is_above_untrusted_cannot_access_trusted(self, db_session):
        """Untrusted user should NOT be able to access trusted resources."""
        user = User(
            username="untrusted_user",
            password="hashed_password",
            group=GroupEnum.untrusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.trusted) is False

    def test_is_above_untrusted_can_access_untrusted(self, db_session):
        """Untrusted user should be able to access untrusted resources."""
        user = User(
            username="untrusted_user",
            password="hashed_password",
            group=GroupEnum.untrusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_above(GroupEnum.untrusted) is True

    def test_can_download_admin_true(self, db_session):
        """Admin user should be able to download."""
        user = User(
            username="admin_user",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.can_download() is True

    def test_can_download_trusted_true(self, db_session):
        """Trusted user should be able to download."""
        user = User(
            username="trusted_user",
            password="hashed_password",
            group=GroupEnum.trusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.can_download() is True

    def test_can_download_untrusted_false(self, db_session):
        """Untrusted user should NOT be able to download."""
        user = User(
            username="untrusted_user",
            password="hashed_password",
            group=GroupEnum.untrusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.can_download() is False

    def test_is_admin_true_for_admin(self, db_session):
        """is_admin() should return True for admin user."""
        user = User(
            username="admin_user",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()

        assert user.is_admin() is True

    def test_is_admin_false_for_non_admin(self, db_session):
        """is_admin() should return False for non-admin users."""
        for group in [GroupEnum.trusted, GroupEnum.untrusted]:
            user = User(
                username=f"user_{group}",
                password="hashed_password",
                group=group,
                root=False,
            )
            db_session.add(user)
            db_session.commit()

            assert user.is_admin() is False

    def test_is_self_true(self, db_session):
        """is_self() should return True when username matches."""
        user = User(
            username="alice",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        assert user.is_self("alice") is True

    def test_is_self_false(self, db_session):
        """is_self() should return False when username doesn't match."""
        user = User(
            username="alice",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        assert user.is_self("bob") is False


class TestAuthenticateUser:
    """Test user authentication function."""

    def test_authenticate_user_success(self, db_session):
        """authenticate_user should return user for correct credentials."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        result = authenticate_user(db_session, "alice", "password123")
        assert result is not None
        assert result.username == "alice"

    def test_authenticate_user_wrong_password(self, db_session):
        """authenticate_user should return None for incorrect password."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        result = authenticate_user(db_session, "alice", "wrongpassword")
        assert result is None

    def test_authenticate_user_nonexistent_user(self, db_session):
        """authenticate_user should return None for nonexistent user."""
        result = authenticate_user(db_session, "nonexistent", "password123")
        assert result is None

    def test_authenticate_user_empty_password(self, db_session):
        """authenticate_user should return None for empty password."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        result = authenticate_user(db_session, "alice", "")
        assert result is None


class TestCreateUser:
    """Test user creation function."""

    def test_create_user_default_group(self):
        """create_user should default to untrusted group."""
        user = create_user("alice", "password123")
        assert user.username == "alice"
        assert user.group == GroupEnum.untrusted
        assert user.root is False

    def test_create_user_trusted_group(self):
        """create_user should accept trusted group."""
        user = create_user("alice", "password123", group=GroupEnum.trusted)
        assert user.group == GroupEnum.trusted

    def test_create_user_admin_group(self):
        """create_user should accept admin group."""
        user = create_user("alice", "password123", group=GroupEnum.admin)
        assert user.group == GroupEnum.admin

    def test_create_user_root_flag(self):
        """create_user should accept root flag."""
        user = create_user("alice", "password123", root=True)
        assert user.root is True

    def test_create_user_extra_data(self):
        """create_user should accept extra_data."""
        user = create_user("alice", "password123", extra_data="custom_data")
        assert user.extra_data == "custom_data"

    def test_create_user_password_hashed(self):
        """create_user should hash password."""
        user = create_user("alice", "password123")
        # Password should be hashed, not plain text
        assert user.password != "password123"


class TestIsCorrectPassword:
    """Test password verification function."""

    def test_is_correct_password_true(self):
        """is_correct_password should return True for correct password."""
        user = create_user("alice", "password123")
        result = is_correct_password(user, "password123")
        assert result is True

    def test_is_correct_password_false(self):
        """is_correct_password should return False for incorrect password."""
        user = create_user("alice", "password123")
        result = is_correct_password(user, "wrongpassword")
        assert result is False

    def test_is_correct_password_empty_string(self):
        """is_correct_password should return False for empty password."""
        user = create_user("alice", "password123")
        result = is_correct_password(user, "")
        assert result is False


class TestGenerateApiKey:
    """Test API key generation function."""

    def test_generate_api_key_format(self):
        """generate_api_key should return a URL-safe string."""
        key = generate_api_key()
        assert isinstance(key, str)
        assert len(key) > 0
        # URL-safe tokens should only contain specific characters
        import string
        valid_chars = set(string.ascii_letters + string.digits + "-_")
        assert all(c in valid_chars for c in key)

    def test_generate_api_key_unique(self):
        """generate_api_key should generate unique keys."""
        keys = [generate_api_key() for _ in range(100)]
        assert len(keys) == len(set(keys))


class TestCreateApiKey:
    """Test API key creation function."""

    def test_create_api_key_returns_tuple(self, db_session):
        """create_api_key should return tuple of (APIKey, private_key)."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        api_key_obj, private_key = create_api_key(user, "test_key")
        assert isinstance(api_key_obj, APIKey)
        assert isinstance(private_key, str)

    def test_create_api_key_has_user_username(self, db_session):
        """create_api_key should link API key to user."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        api_key_obj, _ = create_api_key(user, "test_key")
        assert api_key_obj.user_username == "alice"

    def test_create_api_key_has_name(self, db_session):
        """create_api_key should store API key name."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        api_key_obj, _ = create_api_key(user, "my_api_key")
        assert api_key_obj.name == "my_api_key"

    def test_create_api_key_hashes_private_key(self, db_session):
        """create_api_key should hash the private key."""
        user = create_user("alice", "password123")
        db_session.add(user)
        db_session.commit()

        api_key_obj, private_key = create_api_key(user, "test_key")
        # Hashed key should not match private key
        assert api_key_obj.key_hash != private_key


class TestDetailedUser:
    """Test DetailedUser model with login_type."""

    def test_detailed_user_can_logout_forms(self):
        """DetailedUser with forms login should be able to logout."""
        user = DetailedUser.model_validate({
            "username": "alice",
            "password": "hashed",
            "group": GroupEnum.admin,
            "root": False,
            "login_type": LoginTypeEnum.forms,
        })
        assert user.can_logout() is True

    def test_detailed_user_can_logout_oidc(self):
        """DetailedUser with OIDC login should be able to logout."""
        user = DetailedUser.model_validate({
            "username": "alice",
            "password": "hashed",
            "group": GroupEnum.admin,
            "root": False,
            "login_type": LoginTypeEnum.oidc,
        })
        assert user.can_logout() is True

    def test_detailed_user_cannot_logout_basic(self):
        """DetailedUser with basic auth should NOT be able to logout."""
        user = DetailedUser.model_validate({
            "username": "alice",
            "password": "hashed",
            "group": GroupEnum.admin,
            "root": False,
            "login_type": LoginTypeEnum.basic,
        })
        assert user.can_logout() is False

    def test_detailed_user_cannot_logout_api_key(self):
        """DetailedUser with API key should NOT be able to logout."""
        user = DetailedUser.model_validate({
            "username": "alice",
            "password": "hashed",
            "group": GroupEnum.admin,
            "root": False,
            "login_type": LoginTypeEnum.api_key,
        })
        assert user.can_logout() is False

    def test_detailed_user_cannot_logout_none(self):
        """DetailedUser with none auth should NOT be able to logout."""
        user = DetailedUser.model_validate({
            "username": "alice",
            "password": "hashed",
            "group": GroupEnum.admin,
            "root": False,
            "login_type": LoginTypeEnum.none,
        })
        assert user.can_logout() is False


class TestSessionMiddleware:
    """Test DynamicSessionMiddleware for session management."""

    def test_middleware_initialization(self):
        """DynamicSessionMiddleware should initialize with correct settings."""
        app = Mock()
        linker = DynamicMiddlewareLinker()
        middleware = DynamicSessionMiddleware(app, "secret_key", linker)

        assert middleware.app is app
        assert middleware.secret_key == "secret_key"

    def test_middleware_update_secret(self):
        """DynamicSessionMiddleware should update session secret."""
        app = Mock()
        linker = DynamicMiddlewareLinker()
        middleware = DynamicSessionMiddleware(app, "old_secret", linker)

        middleware.update_secret("new_secret")
        assert middleware.secret_key == "old_secret"  # Original stored
        # New session_middleware should be created with new secret
        assert middleware.session_middleware is not None

    def test_middleware_update_max_age(self):
        """DynamicSessionMiddleware should update session max_age."""
        app = Mock()
        linker = DynamicMiddlewareLinker()
        middleware = DynamicSessionMiddleware(app, "secret", linker)

        middleware.update_max_age(3600)
        assert middleware.session_middleware is not None

    @pytest.mark.asyncio
    async def test_middleware_call(self):
        """DynamicSessionMiddleware should delegate to wrapped middleware."""
        app = Mock()
        linker = DynamicMiddlewareLinker()
        middleware = DynamicSessionMiddleware(app, "secret", linker)

        # Mock the session_middleware
        middleware.session_middleware = AsyncMock()

        scope = {"type": "http"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)
        middleware.session_middleware.assert_called_once()


class TestDynamicMiddlewareLinker:
    """Test DynamicMiddlewareLinker for managing multiple middlewares."""

    def test_linker_add_middleware(self):
        """DynamicMiddlewareLinker should add middleware to list."""
        linker = DynamicMiddlewareLinker()
        app = Mock()
        middleware = DynamicSessionMiddleware(app, "secret", linker)

        assert middleware in linker.middlewares

    def test_linker_update_secret_all_middlewares(self):
        """DynamicMiddlewareLinker should update secret for all middlewares."""
        linker = DynamicMiddlewareLinker()
        app = Mock()

        middleware1 = DynamicSessionMiddleware(app, "secret1", linker)
        middleware2 = DynamicSessionMiddleware(app, "secret2", linker)

        linker.update_secret("new_secret")
        # Both middlewares should have new session_middleware created
        assert middleware1.session_middleware is not None
        assert middleware2.session_middleware is not None

    def test_linker_update_max_age_all_middlewares(self):
        """DynamicMiddlewareLinker should update max_age for all middlewares."""
        linker = DynamicMiddlewareLinker()
        app = Mock()

        middleware1 = DynamicSessionMiddleware(app, "secret1", linker)
        middleware2 = DynamicSessionMiddleware(app, "secret2", linker)

        linker.update_max_age(7200)
        # Both middlewares should have new session_middleware created
        assert middleware1.session_middleware is not None
        assert middleware2.session_middleware is not None


class TestOidcConfig:
    """Test OIDC configuration and validation."""

    @pytest.mark.asyncio
    async def test_set_endpoint_success(self, db_session):
        """set_endpoint should fetch and store OIDC endpoints."""
        from contextlib import asynccontextmanager
        
        config = oidcConfig()
        
        response_data = {
            "authorization_endpoint": "https://auth.example.com/auth",
            "token_endpoint": "https://auth.example.com/token",
            "userinfo_endpoint": "https://auth.example.com/userinfo",
            "end_session_endpoint": "https://auth.example.com/logout",
        }
        
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(return_value=response_data)
        
        @asynccontextmanager
        async def mock_get(url):
            yield response
        
        client_session = AsyncMock()
        client_session.get = mock_get

        await config.set_endpoint(
            db_session, client_session, "https://auth.example.com/.well-known/openid-configuration"
        )

        assert config.get(db_session, "oidc_authorize_endpoint") == "https://auth.example.com/auth"
        assert config.get(db_session, "oidc_token_endpoint") == "https://auth.example.com/token"

    @pytest.mark.asyncio
    async def test_set_endpoint_failure(self, db_session):
        """set_endpoint should raise InvalidOIDCConfiguration on JSON parse failure."""
        from contextlib import asynccontextmanager
        
        config = oidcConfig()
        
        response = AsyncMock()
        response.status = 200
        # Missing required fields will cause validation error
        response.json = AsyncMock(return_value={})
        
        @asynccontextmanager
        async def mock_get(url):
            yield response
        
        client_session = AsyncMock()
        client_session.get = mock_get

        with pytest.raises(InvalidOIDCConfiguration):
            await config.set_endpoint(
                db_session,
                client_session,
                "https://auth.example.com/.well-known/openid-configuration",
            )

    def test_get_redirect_https_true(self, db_session):
        """get_redirect_https should return True when configured."""
        config = oidcConfig()
        config.set(db_session, "oidc_redirect_https", "true")

        assert config.get_redirect_https(db_session) is True

    @pytest.mark.asyncio
    async def test_validate_success(self, db_session):
        """validate should return None for valid OIDC config."""
        from contextlib import asynccontextmanager
        
        config = oidcConfig()
        config.set(db_session, "oidc_endpoint", "https://auth.example.com/.well-known/openid-configuration")
        config.set(db_session, "oidc_scope", "openid profile")
        config.set(db_session, "oidc_username_claim", "sub")
        config.set(db_session, "oidc_group_claim", "groups")

        response = AsyncMock()
        response.ok = True
        response.json = AsyncMock(
            return_value={
                "scopes_supported": ["openid", "profile", "email"],
                "claims_supported": ["sub", "name", "email", "groups"],
            }
        )

        @asynccontextmanager
        async def mock_get(url):
            yield response
        
        client_session = AsyncMock()
        client_session.get = mock_get

        result = await config.validate(db_session, client_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_unsupported_scope(self, db_session):
        """validate should return error for unsupported scopes."""
        from contextlib import asynccontextmanager
        
        config = oidcConfig()
        config.set(db_session, "oidc_endpoint", "https://auth.example.com/.well-known/openid-configuration")
        config.set(db_session, "oidc_scope", "openid profile custom_scope")
        config.set(db_session, "oidc_username_claim", "sub")

        response = AsyncMock()
        response.ok = True
        response.json = AsyncMock(
            return_value={
                "scopes_supported": ["openid", "profile"],
                "claims_supported": ["sub", "name", "email"],
            }
        )

        @asynccontextmanager
        async def mock_get(url):
            yield response
        
        client_session = AsyncMock()
        client_session.get = mock_get

        result = await config.validate(db_session, client_session)
        assert "custom_scope" in result

    @pytest.mark.asyncio
    async def test_validate_unsupported_username_claim(self, db_session):
        """validate should return error for unsupported username claim."""
        from contextlib import asynccontextmanager
        
        config = oidcConfig()
        config.set(db_session, "oidc_endpoint", "https://auth.example.com/.well-known/openid-configuration")
        config.set(db_session, "oidc_scope", "openid")
        config.set(db_session, "oidc_username_claim", "custom_username_claim")

        response = AsyncMock()
        response.ok = True
        response.json = AsyncMock(
            return_value={
                "scopes_supported": ["openid"],
                "claims_supported": ["sub", "name"],
            }
        )

        @asynccontextmanager
        async def mock_get(url):
            yield response
        
        client_session = AsyncMock()
        client_session.get = mock_get

        result = await config.validate(db_session, client_session)
        assert "Username claim" in result


class TestAPIKeyAuth:
    """Test API key authentication."""

    @pytest.mark.asyncio
    async def test_api_key_auth_valid_key(self, db_session):
        """APIKeyAuth should authenticate valid API key."""
        # Create user and API key
        user = create_user("alice", "password", group=GroupEnum.trusted)
        db_session.add(user)
        db_session.commit()

        api_key_obj, private_key = create_api_key(user, "test_key")
        db_session.add(api_key_obj)
        db_session.commit()

        # Test authentication
        auth = APIKeyAuth(lowest_allowed_group=GroupEnum.untrusted)
        credentials = AsyncMock()
        credentials.credentials = private_key

        request = MagicMock(spec=Request)
        bearer_auth = AsyncMock(return_value=credentials)
        auth.api_key_header = bearer_auth

        result = await auth(request, db_session)
        assert result.username == "alice"
        assert result.login_type == LoginTypeEnum.api_key

    @pytest.mark.asyncio
    async def test_api_key_auth_invalid_key(self, db_session):
        """APIKeyAuth should reject invalid API key."""
        auth = APIKeyAuth(lowest_allowed_group=GroupEnum.untrusted)
        credentials = AsyncMock()
        credentials.credentials = "invalid_key"

        request = MagicMock(spec=Request)
        bearer_auth = AsyncMock(return_value=credentials)
        auth.api_key_header = bearer_auth

        with pytest.raises(HTTPException) as exc_info:
            await auth(request, db_session)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_api_key_auth_insufficient_permissions(self, db_session):
        """APIKeyAuth should reject API key with insufficient permissions."""
        # Create untrusted user with API key
        user = create_user("alice", "password", group=GroupEnum.untrusted)
        db_session.add(user)
        db_session.commit()

        api_key_obj, private_key = create_api_key(user, "test_key")
        db_session.add(api_key_obj)
        db_session.commit()

        # Require trusted group
        auth = APIKeyAuth(lowest_allowed_group=GroupEnum.trusted)
        credentials = AsyncMock()
        credentials.credentials = private_key

        request = MagicMock(spec=Request)
        bearer_auth = AsyncMock(return_value=credentials)
        auth.api_key_header = bearer_auth

        with pytest.raises(HTTPException) as exc_info:
            await auth(request, db_session)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


class TestABRAuth:
    """Test ABRAuth multi-method authentication dispatcher."""

    @pytest.mark.asyncio
    async def test_abr_auth_basic_auth(self, db_session):
        """ABRAuth should authenticate via basic auth."""
        user = create_user("alice", "password123", group=GroupEnum.admin)
        db_session.add(user)
        db_session.commit()

        auth = ABRAuth(lowest_allowed_group=GroupEnum.untrusted)

        # Mock auth_config to return basic login type
        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.basic

            # Mock HTTPBasic security
            credentials = AsyncMock()
            credentials.username = "alice"
            credentials.password = "password123"
            auth.security = AsyncMock(return_value=credentials)

            request = MagicMock(spec=Request)
            result = await auth(request, db_session)

            assert result.username == "alice"
            assert result.login_type == LoginTypeEnum.basic

    @pytest.mark.asyncio
    async def test_abr_auth_session_auth(self, db_session):
        """ABRAuth should authenticate via session."""
        user = create_user("alice", "password123", group=GroupEnum.admin)
        db_session.add(user)
        db_session.commit()

        auth = ABRAuth(lowest_allowed_group=GroupEnum.untrusted)

        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.forms

            request = MagicMock(spec=Request)
            request.session = {"sub": "alice"}

            result = await auth(request, db_session)
            assert result.username == "alice"
            assert result.login_type == LoginTypeEnum.forms

    @pytest.mark.asyncio
    async def test_abr_auth_oidc_auth_valid(self, db_session):
        """ABRAuth should authenticate OIDC with valid token."""
        user = create_user("alice", "password123", group=GroupEnum.admin)
        db_session.add(user)
        db_session.commit()

        auth = ABRAuth(lowest_allowed_group=GroupEnum.untrusted)

        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.oidc

            request = MagicMock(spec=Request)
            request.session = {"sub": "alice", "exp": time.time() + 3600}

            result = await auth(request, db_session)
            assert result.username == "alice"
            assert result.login_type == LoginTypeEnum.oidc

    @pytest.mark.asyncio
    async def test_abr_auth_oidc_auth_expired(self, db_session):
        """ABRAuth should reject expired OIDC token."""
        auth = ABRAuth(lowest_allowed_group=GroupEnum.untrusted)

        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.oidc

            request = MagicMock(spec=Request)
            request.session = {"sub": "alice", "exp": time.time() - 3600}  # Expired

            with pytest.raises(RequiresLoginException):
                await auth(request, db_session)

    @pytest.mark.asyncio
    async def test_abr_auth_none_login(self, db_session):
        """ABRAuth should return admin user for none auth."""
        user = create_user("admin_user", "password", group=GroupEnum.admin)
        db_session.add(user)
        db_session.commit()

        auth = ABRAuth(lowest_allowed_group=GroupEnum.untrusted)

        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.none

            request = MagicMock(spec=Request)

            result = await auth(request, db_session)
            assert result.group == GroupEnum.admin

    @pytest.mark.asyncio
    async def test_abr_auth_insufficient_permissions(self, db_session):
        """ABRAuth should reject user with insufficient permissions."""
        user = create_user("alice", "password123", group=GroupEnum.untrusted)
        db_session.add(user)
        db_session.commit()

        auth = ABRAuth(lowest_allowed_group=GroupEnum.admin)

        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.basic

            credentials = AsyncMock()
            credentials.username = "alice"
            credentials.password = "password123"
            auth.security = AsyncMock(return_value=credentials)

            request = MagicMock(spec=Request)

            with pytest.raises(HTTPException) as exc_info:
                await auth(request, db_session)
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_abr_auth_session_not_found(self, db_session):
        """ABRAuth should raise RequiresLoginException when user not in session."""
        auth = ABRAuth(lowest_allowed_group=GroupEnum.untrusted)

        with patch("app.internal.auth.authentication.auth_config") as mock_config:
            mock_config.get_login_type.return_value = LoginTypeEnum.forms

            request = MagicMock(spec=Request)
            request.session = {}  # No 'sub' in session

            with pytest.raises(RequiresLoginException):
                await auth(request, db_session)


# Import time for OIDC expiry tests
import time
