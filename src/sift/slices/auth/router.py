# SPDX-License-Identifier: AGPL-3.0-or-later
"""The auth endpoints, and the dependencies every other slice reads them through.

`current_viewer` resolves the session cookie to a user from the database on every request.
A state-changing request also passes `csrf_protect`; with the SameSite cookie, that is the CSRF
defence, neither half trusted alone.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from starlette.requests import HTTPConnection

from sift.kernel import lifecycle, wiring
from sift.kernel.access import Concealment, Viewer
from sift.kernel.client import (
    DEVICE_COOKIE_NAME,
    DEVICE_COOKIE_SECONDS,
    Client,
    client_of,
    new_device,
)
from sift.kernel.client import enter as enter_client
from sift.kernel.config import Settings
from sift.kernel.http import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    is_https,
    reached_over_the_local_network,
)
from sift.kernel.wiring import ACCESS, SETTINGS_HUB, part_of
from sift.slices.auth.crypto import derive_csrf_token, hash_token, tokens_equal
from sift.slices.auth.errors import (
    InvalidCredentials,
    LockedOut,
    MasterKeyCorrupted,
    NoSuchUser,
    NotAGuestUser,
    SetupAlreadyDone,
    SignInBusy,
    TooManyRenames,
    UsernameTaken,
)
from sift.slices.auth.models import (
    GeneratedUserResponse,
    GuestCreateRequest,
    LockResponse,
    LoginRequest,
    PasswordChangeRequest,
    PasswordCheckRequest,
    PasswordCheckResponse,
    PasswordResetRequest,
    PasswordUnlockRequest,
    PinSetRequest,
    PinVerifyRequest,
    SetupRequest,
    SetupStatusResponse,
    UnlockSecretsRequest,
    UserDisabledRequest,
    UsernameChangeRequest,
    UserResponse,
    ViewerResponse,
)
from sift.slices.auth.passwords import PasswordPolicyError, is_breached, validate_password
from sift.slices.auth.service import SERVICE, Authenticated, AuthService, User
from sift.slices.auth.tuning import (
    CLEAR_CACHE_ON_SIGN_OUT,
    SESSION_DAYS_KEY,
    VAULT_CONCEALMENT_KEY,
    session_seconds_from,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Wiring --------------------------------------------------------------------------------


def _token_from(connection: HTTPConnection) -> str:
    """The session credential this connection carries, or empty, read in one place."""
    return connection.cookies.get(SESSION_COOKIE_NAME) or ""


def _service(connection: HTTPConnection) -> AuthService:
    """The auth service, off any connection: a WebSocket carries the same cookie."""
    return part_of(connection, SERVICE)


# --- Dependencies other slices use ---------------------------------------------------------


class SettingsReader(Protocol):
    """The one thing session resolution needs from the preference store, typed structurally."""

    async def get_user(self, user_id: str, key: str) -> Any: ...


async def _concealment_for(connection: HTTPConnection, user_id: str) -> Concealment:
    """How this user has asked the vault to hide things, read on every request."""
    hub = part_of(connection, SETTINGS_HUB)
    return Concealment(await hub.get_user(user_id, VAULT_CONCEALMENT_KEY))


async def _vault_view(connection: HTTPConnection, viewer: Viewer, *, unlocked: bool) -> Viewer:
    """Settle both Hidden facts for this viewer: whether it is open, and how it conceals.

    Every user gets both: what a viewer hid is hidden only from that viewer.
    """
    return replace(
        viewer,
        show_hidden=unlocked,
        concealment=await _concealment_for(connection, viewer.id),
    )


#: 423, not 401: a 401 makes a browser drop the session the PIN would open.
LOCKED_SESSION = "Sift is locked on this session"

#: Marks this 423 as the whole session shut, not one file in a vault (`kernel.reach.vault_locked`).
#: The mark is on this one, so a refusal that forgets it reads as the ordinary kind.
SESSION_LOCKED_MARK = {"Sift-Locked": "session"}


@dataclass(frozen=True, slots=True)
class SessionViewer:
    """Who is behind a connection and whether Sift is locked on it; a socket cannot get a 423."""

    viewer: Viewer | None
    locked: bool


async def viewer_on(connection: HTTPConnection) -> SessionViewer:
    """The user behind this connection, rebuilt from the database, or None.

    The one place a session becomes a viewer, sockets included, with both Hidden facts settled.
    """
    token = _token_from(connection)
    if not token:
        return SessionViewer(None, locked=False)
    service = _service(connection)
    resolved = await service.resolve_session_state(token)
    if resolved is None:
        return SessionViewer(None, locked=False)
    # Built closed, then opened as far as this browser has opened it.
    viewer = await part_of(connection, ACCESS).load_viewer(resolved.user_id)
    if viewer is None:
        return SessionViewer(None, locked=resolved.locked)
    unlocked = service.vault_unlocks.is_unlocked(hash_token(token))
    # Which window and device made this request, for the writers that stamp it.
    enter_client(client_of(connection))
    return SessionViewer(
        await _vault_view(connection, viewer, unlocked=unlocked), locked=resolved.locked
    )


async def _resolve_viewer(request: Request, *, allow_locked: bool = False) -> Viewer | None:
    """The user behind the session, or None; a locked session is refused (423) unless allowed."""
    found = await viewer_on(request)
    if found.locked and not allow_locked:
        raise HTTPException(status.HTTP_423_LOCKED, LOCKED_SESSION, headers=SESSION_LOCKED_MARK)
    return found.viewer


#: Marks a 401 as no session, not a wrong credential, so a mistyped password signs nobody out.
SESSION_CHALLENGE = {"WWW-Authenticate": "Session"}


async def current_viewer(request: Request) -> Viewer:
    """The signed-in user, or a 401. The dependency a scoped route asks for."""
    viewer = await _resolve_viewer(request)
    if viewer is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "not signed in", headers=SESSION_CHALLENGE
        )
    return viewer


async def optional_viewer(request: Request) -> Viewer | None:
    """The signed-in user, or None; a locked session is refused rather than read as nobody."""
    return await _resolve_viewer(request)


async def viewer_even_if_locked(request: Request) -> Viewer:
    """The signed-in user, locked or not: for unlocking, signing out and asking who you are."""
    viewer = await _resolve_viewer(request, allow_locked=True)
    if viewer is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "not signed in", headers=SESSION_CHALLENGE
        )
    return viewer


async def require_admin(
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Viewer:
    """An admin, or a 403, enforced here on the server."""
    if not viewer.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admins only")
    return viewer


async def master_key(request: Request) -> bytes | None:
    """The unwrapped master key for this session, or None, which means wait for a login."""
    viewer = await _resolve_viewer(request)
    if viewer is None:
        return None
    return _service(request).master_keys.get(viewer.id)


def session_key(request: Request) -> str | None:
    """This session's handle in the vault-unlock store: the hashed token, never the token."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return None if token is None else hash_token(token)


async def require_vault_pin(request: Request, viewer: Viewer) -> None:
    """Refuse to conceal anything for a user with no PIN, which alone opens the vault."""
    if not await _service(request).has_pin(viewer.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "set a PIN before using the vault \u2014 it's the only thing that opens it again",
        )


async def csrf_protect(request: Request) -> None:
    """Refuse a state-changing request whose header lacks the session's CSRF token."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if token is None or header is None or not tokens_equal(derive_csrf_token(token), header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")


# --- Helpers -------------------------------------------------------------------------------


async def _session_seconds(request: Request) -> int:
    """How long a sign-in lasts, read now from the setting, for both the row and the cookie."""
    return session_seconds_from(await part_of(request, SETTINGS_HUB).get_app(SESSION_DAYS_KEY))


def _set_session_cookie(
    request: Request, response: Response, settings: Settings, token: str, ttl_seconds: int
) -> None:
    secure = (
        settings.cookie_secure
        if settings.cookie_secure is not None
        else is_https(request.url.scheme, request.headers.get("x-forwarded-proto", ""))
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _give_device(request: Request, response: Response, settings: Settings) -> Client:
    """Give this browser its device id cookie, keeping one it already has, and answer the client."""
    found = client_of(request)
    device = found.device or new_device()
    secure = (
        settings.cookie_secure
        if settings.cookie_secure is not None
        else is_https(request.url.scheme, request.headers.get("x-forwarded-proto", ""))
    )
    response.set_cookie(
        key=DEVICE_COOKIE_NAME,
        value=device,
        max_age=DEVICE_COOKIE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return Client(found.kind, device)


# Written out: importing it back from `browse` or `settings_hub` would be a cycle.
_CAN_SAVE_KEY = "guests.can_save_to_device"


async def _can_save_to_device(request: Request, *, is_admin: bool) -> bool:
    """Whether this viewer may save originals: an admin always, a guest when turned on."""
    if is_admin:
        return True
    return bool(await part_of(request, SETTINGS_HUB).get_app(_CAN_SAVE_KEY))


def _viewer_response(result: Authenticated, *, can_save_to_device: bool) -> ViewerResponse:
    return ViewerResponse(
        id=result.user_id,
        username=result.username,
        role=result.role,
        csrf_token=result.csrf_token,
        can_save_to_device=can_save_to_device,
    )


# --- Routes --------------------------------------------------------------------------------


@router.get("/status")
async def setup_status(
    service: Annotated[AuthService, Depends(_service)],
) -> SetupStatusResponse:
    """Whether the instance still needs its admin; the one thing a signed-out client may ask."""
    return SetupStatusResponse(needs_setup=not await service.admin_exists())


@router.post("/password/check")
async def check_password(body: PasswordCheckRequest) -> PasswordCheckResponse:
    """What the policy makes of a password being typed, leaked list included; never logged."""
    breached = is_breached(body.password)
    try:
        validate_password(body.password)
    except PasswordPolicyError as exc:
        return PasswordCheckResponse(acceptable=False, breached=breached, reason=str(exc))
    return PasswordCheckResponse(acceptable=True, breached=False)


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(wiring.settings)],
    service: Annotated[AuthService, Depends(_service)],
) -> ViewerResponse:
    """Create the one admin, on first run only; a second call is a 409."""
    try:
        seconds = await _session_seconds(request)
        result = await service.create_first_admin(body.username, body.password, ttl_seconds=seconds)
    except SetupAlreadyDone as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "setup has already been completed") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    _set_session_cookie(request, response, settings, result.session_token, seconds)
    await service.note_device(result.session_token, _give_device(request, response, settings))
    can_save = await _can_save_to_device(request, is_admin=result.role == "admin")
    return _viewer_response(result, can_save_to_device=can_save)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(wiring.settings)],
    service: Annotated[AuthService, Depends(_service)],
) -> ViewerResponse:
    """Sign in; a failure answers the same, in the same time, whatever was wrong."""
    try:
        seconds = await _session_seconds(request)
        result = await service.login(
            body.username,
            body.password,
            ttl_seconds=seconds,
            client=request.client.host if request.client is not None else None,
        )
    except InvalidCredentials as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Incorrect username or password."
        ) from exc
    except SignInBusy as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    _set_session_cookie(request, response, settings, result.session_token, seconds)
    await service.note_device(result.session_token, _give_device(request, response, settings))
    can_save = await _can_save_to_device(request, is_admin=result.role == "admin")
    return _viewer_response(result, can_save_to_device=can_save)


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def logout(
    request: Request,
    response: Response,
    viewer: Annotated[Viewer, Depends(viewer_even_if_locked)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Sign out, revoking the session in the database; reachable from a locked session."""
    # `current_viewer` resolved a session, so the handle is there.
    await service.logout(request.cookies.get(SESSION_COOKIE_NAME) or "")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.headers["Clear-Site-Data"] = CLEAR_CACHE_ON_SIGN_OUT
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
async def me(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(wiring.settings)],
    viewer: Annotated[Viewer, Depends(viewer_even_if_locked)],
    service: Annotated[AuthService, Depends(_service)],
) -> ViewerResponse:
    """Who the caller is and the CSRF token, read from the row; answered while locked too."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    csrf = derive_csrf_token(token) if token is not None else ""
    if client_of(request).device is None:
        await service.note_device(token or "", _give_device(request, response, settings))
    username = await service.username_of(viewer.id) or ""
    can_save = await _can_save_to_device(request, is_admin=viewer.is_admin)
    return ViewerResponse(
        id=viewer.id,
        username=username,
        role=viewer.role.value,
        csrf_token=csrf,
        can_save_to_device=can_save,
        secrets_locked=service.secrets_locked(viewer.id),
        pin_unlock_offered=_over_the_local_network(request)
        and await service.may_reopen_with_pin(viewer.id),
        locked=await service.is_locked(_token_from(request)),
        boot=lifecycle.BOOT_ID if viewer.is_admin else None,
    )


@router.post(
    "/unlock-secrets", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def unlock_secrets(
    body: UnlockSecretsRequest,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[AuthService, Depends(_service)],
) -> None:
    """Put this user's master key back in memory, keeping the session."""
    if not await service.unlock_secrets(viewer.id, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "password is incorrect")


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Change your own password, the old one required; every other session is revoked."""
    current = request.cookies.get(SESSION_COOKIE_NAME)
    try:
        await service.change_password(
            viewer.id, body.old_password, body.new_password, current_token=current
        )
    except InvalidCredentials as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "old password is incorrect") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except MasterKeyCorrupted as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "the user's key couldn't be updated"
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/pin", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)])
async def set_pin(
    body: PinSetRequest,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Set or change the PIN; the current password is required."""
    try:
        await service.set_pin(viewer.id, body.pin, body.current_password)
    except InvalidCredentials as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "password is incorrect") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- User management (admin) -----------------------------------------------------------------
#
# Admin routes acting on somebody else; the service refuses any user who is not a guest.


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        disabled=user.disabled,
        created_at=user.created_at,
    )


def _user_gone() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "there's no such user")


def _not_a_guest() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "only guest users can be managed here")


@router.get("/users")
async def list_users(
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[AuthService, Depends(_service)],
) -> list[UserResponse]:
    """Every user on this instance, for an admin only."""
    return [_user_response(user) for user in await service.list_users()]


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
async def create_guest(
    body: GuestCreateRequest,
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[AuthService, Depends(_service)],
) -> UserResponse:
    """Add a guest with a first password an admin chooses; this route makes no admin."""
    try:
        user = await service.create_guest(admin, body.username, body.password)
    except UsernameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _user_response(user)


@router.post(
    "/users/generate",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
async def generate_guest(
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[AuthService, Depends(_service)],
) -> GeneratedUserResponse:
    """Add a guest with an invented name and password; the password is in this reply only."""
    try:
        generated = await service.generate_guest(admin)
    except UsernameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return GeneratedUserResponse(user=_user_response(generated.user), password=generated.password)


@router.post("/users/{user_id}/username", dependencies=[Depends(csrf_protect)])
async def rename_user(
    user_id: str,
    body: UsernameChangeRequest,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[AuthService, Depends(_service)],
) -> UserResponse:
    """Change what a user is called: your own always, anybody's as an admin."""
    if user_id != viewer.id and not viewer.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you can only change your own username")
    try:
        return _user_response(
            await service.rename_user(
                user_id, body.username, counted_against_admin=viewer.is_admin, by=viewer
            )
        )
    except NoSuchUser as exc:
        raise _user_gone() from exc
    except TooManyRenames as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except UsernameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.put("/users/{user_id}/disabled", dependencies=[Depends(csrf_protect)])
async def set_user_disabled(
    user_id: str,
    body: UserDisabledRequest,
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[AuthService, Depends(_service)],
) -> UserResponse:
    """Stop a user signing in, or let them again, from their very next request."""
    try:
        user = await service.set_user_disabled(admin, user_id, body.disabled)
    except NoSuchUser as exc:
        raise _user_gone() from exc
    except NotAGuestUser as exc:
        raise _not_a_guest() from exc
    return _user_response(user)


@router.post(
    "/users/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def reset_user_password(
    user_id: str,
    body: PasswordResetRequest,
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Set a guest's password without the old one, and end their sessions."""
    try:
        await service.reset_user_password(user_id, body.new_password)
    except NoSuchUser as exc:
        raise _user_gone() from exc
    except NotAGuestUser as exc:
        raise _not_a_guest() from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_user(
    user_id: str,
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Remove a guest, their sessions and every share and restrict made to them."""
    try:
        await service.delete_user(admin, user_id)
    except NoSuchUser as exc:
        raise _user_gone() from exc
    except NotAGuestUser as exc:
        raise _not_a_guest() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the app lock ------------------------------------------------------------------------------

#: Why the PIN was refused off the local network; the password always works.
PIN_NEEDS_THE_LOCAL_NETWORK = (
    "Your PIN only unlocks Sift on your local network. From here, use your password."
)


def _over_the_local_network(request: Request) -> bool:
    """Whether this request may be answered with a PIN. See `reached_over_the_local_network`."""
    return reached_over_the_local_network(
        request.client.host if request.client is not None else None, request.headers.keys()
    )


@router.post("/lock", dependencies=[Depends(csrf_protect)])
async def lock_app(
    request: Request,
    viewer: Annotated[Viewer, Depends(viewer_even_if_locked)],
    service: Annotated[AuthService, Depends(_service)],
) -> LockResponse:
    """Shut this session, and say which way it was shut.

    Decided here, where the setting and the PIN are. Allowed on a session already locked, so a
    second press is not worse than the first; it never fails.
    """
    del viewer
    outcome = await service.lock_app(request.cookies.get(SESSION_COOKIE_NAME) or "")
    return LockResponse(outcome=outcome.value)


@router.post("/unlock/password", dependencies=[Depends(csrf_protect)])
async def unlock_app_with_password(
    body: PasswordUnlockRequest,
    request: Request,
    viewer: Annotated[Viewer, Depends(viewer_even_if_locked)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Open a locked session with the password, always available; Hidden stays shut."""
    del viewer
    ok = await service.unlock_app_with_password(_token_from(request), body.password)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "password is incorrect")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/unlock", dependencies=[Depends(csrf_protect)])
async def unlock_app(
    body: PinVerifyRequest,
    request: Request,
    viewer: Annotated[Viewer, Depends(viewer_even_if_locked)],
    service: Annotated[AuthService, Depends(_service)],
) -> Response:
    """Open a locked session with the PIN, verifying and clearing the lock in one step.

    It never mints a session, and Hidden stays shut. A run of wrong PINs ends the session; only
    from the local network.
    """
    del viewer
    if not _over_the_local_network(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, PIN_NEEDS_THE_LOCAL_NETWORK)
    try:
        ok = await service.unlock_app(request.cookies.get(SESSION_COOKIE_NAME) or "", body.pin)
    except LockedOut as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts, try again later"
        ) from exc
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect PIN.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
