"""Small WorkOS SDK adapter used by auth routes and product mutations."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from agent.app.config import Settings, get_settings
from agent.app.logging_utils import get_logger

logger = get_logger("agent.product.workos_client")


class WorkOSClientAdapter:
    def __init__(self, settings: Settings):
        if not settings.workos_api_key or not settings.workos_client_id:
            raise RuntimeError("WorkOS requires ARCHAGENT_WORKOS_API_KEY and ARCHAGENT_WORKOS_CLIENT_ID.")
        try:
            from workos import WorkOSClient
        except ImportError as exc:
            raise RuntimeError("WorkOS authentication requires the workos package.") from exc
        self.settings = settings
        self.client = WorkOSClient(api_key=settings.workos_api_key, client_id=settings.workos_client_id)

    def authorization_url(self, state: str | None = None) -> str:
        return self.client.user_management.get_authorization_url(
            provider="authkit",
            redirect_uri=self.settings.workos_redirect_uri,
            prompt="login",
            state=state,
        )

    def authenticate_with_code(self, code: str) -> Any:
        from workos.session import seal_session_from_auth_response

        response = self.client.user_management.authenticate_with_code(code=code)
        response_data = response.to_dict()
        response_data["sealed_session"] = seal_session_from_auth_response(
            access_token=response_data["access_token"],
            refresh_token=response_data["refresh_token"],
            user=response_data["user"],
            impersonator=response_data.get("impersonator"),
            cookie_password=self._cookie_password(),
        )
        return response_data

    def load_session(self, sealed_session: str) -> Any:
        return self.client.user_management.load_sealed_session(
            session_data=sealed_session,
            cookie_password=self._cookie_password(),
        )

    def send_invitation(self, *, email: str, organization_id: str, role: str, inviter_user_id: str | None) -> Any:
        return self.client.user_management.send_invitation(
            email=email,
            organization_id=organization_id,
            role_slug=role,
            inviter_user_id=inviter_user_id,
        )

    def provision_self_serve_organization(
        self,
        *,
        workos_user_id: str,
        email: str,
        name: str,
        role: str = "owner",
    ) -> dict[str, str | None]:
        """Create the WorkOS organization and owner membership for a direct signup."""
        organization_name = self._workspace_name(email=email, name=name)
        external_id = f"archagent:self-serve:{workos_user_id}"
        try:
            organization = self.client.organizations.get_organization_by_external_id(external_id)
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            organization = self.client.organizations.create_organization(
                name=organization_name,
                external_id=external_id,
            )
        organization_id = str(get_field(organization, "id"))
        membership = self._create_organization_membership(
            workos_user_id=workos_user_id,
            workos_organization_id=organization_id,
            role_slug=self.settings.workos_self_serve_role_slug,
        )
        membership_id = get_field(membership, "id")
        return {
            "organization_id": organization_id,
            "organization_name": str(get_field(organization, "name", default=organization_name) or organization_name),
            "membership_id": str(membership_id) if membership_id else None,
            "role": role,
        }

    def revoke_invitation(self, invitation_id: str) -> bool:
        try:
            self.client.user_management.revoke_invitation(invitation_id)
            return True
        except Exception as exc:
            if self._is_invitation_not_pending(exc):
                logger.warning(
                    "workos invitation is not pending; clearing local invitation invitation_id=%s",
                    invitation_id,
                )
                return False
            raise

    def update_membership(self, membership_id: str, role: str) -> Any:
        return self.client.organization_membership.update_organization_membership(membership_id, role=self._role_value(role))

    def deactivate_membership(self, membership_id: str) -> Any:
        return self.client.organization_membership.deactivate_organization_membership(membership_id)

    def construct_webhook_event(self, payload: bytes, signature: str) -> Any:
        if not self.settings.workos_webhook_secret:
            raise RuntimeError("ARCHAGENT_WORKOS_WEBHOOK_SECRET is required.")
        if hasattr(self.client.webhooks, "verify_event"):
            return self.client.webhooks.verify_event(
                event_body=payload,
                event_signature=self._normalize_webhook_signature(signature),
                secret=self.settings.workos_webhook_secret,
            )
        if hasattr(self.client.webhooks, "construct_event"):
            return self.client.webhooks.construct_event(
                payload=payload,
                sig_header=signature,
                secret=self.settings.workos_webhook_secret,
            )
        raise RuntimeError("WorkOS SDK does not expose webhook event verification.")

    def list_events(self, *, after: str | None = None) -> Any:
        params = {"after": after} if after else {}
        return self.client.events.list_events(**params)

    def _cookie_password(self) -> str:
        if not self.settings.workos_cookie_password:
            raise RuntimeError("ARCHAGENT_WORKOS_COOKIE_PASSWORD is required for WorkOS browser sessions.")
        return self.settings.workos_cookie_password

    def _create_organization_membership(
        self,
        *,
        workos_user_id: str,
        workos_organization_id: str,
        role_slug: str | None,
    ) -> Any:
        create = self.client.organization_membership.create_organization_membership
        kwargs = {
            "user_id": workos_user_id,
            "organization_id": workos_organization_id,
        }
        if role_slug:
            kwargs["role"] = self._role_value(role_slug)
        try:
            return create(**kwargs)
        except TypeError:
            kwargs.pop("role", None)
            if role_slug:
                kwargs["role_slug"] = role_slug
            return create(**kwargs)
        except Exception as exc:
            if role_slug and self._is_invalid_role(exc):
                logger.warning(
                    "workos self-serve role invalid; retrying with default role role_slug=%s",
                    role_slug,
                )
                return self._create_organization_membership(
                    workos_user_id=workos_user_id,
                    workos_organization_id=workos_organization_id,
                    role_slug=None,
                )
            raise

    def _role_value(self, role: str) -> Any:
        try:
            from workos.organization_membership._resource import RoleSingle
        except Exception:
            return role
        return RoleSingle(role_slug=role)

    @staticmethod
    def _normalize_webhook_signature(signature: str) -> str:
        """WorkOS v8 expects the timestamp/signature pair separated by comma+space."""
        return ", ".join(part.strip() for part in signature.split(","))

    @staticmethod
    def _workspace_name(*, email: str, name: str) -> str:
        clean_name = name.strip()
        if clean_name and clean_name.lower() != email.lower().strip():
            return f"{clean_name}'s Workspace"
        domain = email.split("@", 1)[1].strip() if "@" in email else ""
        return f"{domain or email.strip() or 'ArchAgent'} Workspace"

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
        return status_code == 404 or exc.__class__.__name__ == "NotFoundError"

    @staticmethod
    def _is_invalid_role(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        message = str(getattr(exc, "message", "") or exc)
        return code == "invalid_role" or "role is invalid" in message.lower()

    @staticmethod
    def _is_invitation_not_pending(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        message = str(getattr(exc, "message", "") or exc)
        return code == "invite_not_pending" or "invite is not pending" in message.lower()


@lru_cache
def get_workos_client() -> WorkOSClientAdapter:
    return WorkOSClientAdapter(get_settings())


def get_field(value: Any, *names: str, default: Any = None) -> Any:
    """Read SDK model attributes or webhook dictionaries without coupling to one casing."""
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default
