# SPDX-License-Identifier: AGPL-3.0-or-later
"""The auth endpoints, and the dependencies every other slice reads them through.

`current_viewer` is the one that matters to the rest of the app: it resolves the session cookie to
a user by reading the database, and every scoped route depends on it. It never trusts anything
the client says about who it is: the role comes from `load_viewer`, which reads it from the row
and refuses a disabled user, so a forged cookie claiming to be an admin resolves to whatever the
database actually says, which is nothing.

State-changing requests also pass `csrf_protect`. A browser will not send the SameSite session
cookie across origins and will not let a foreign page read it, so a cross-site page can neither
authenticate the request nor produce the header that must match the cookie-bound token. The two
together are the CSRF defence; neither alone is trusted to be.
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
from sift.slices.auth.service import (
    SERVICE,
    Authenticated,
    AuthService,
    InvalidCredentials,
    LockedOut,
    MasterKeyCorrupted,
    NoSuchUser,
    NotAGuestUser,
    SetupAlreadyDone,
    SignInBusy,
    TooManyRenames,
    User,
    UsernameTaken,
)
from sift.slices.auth.tuning import (
    CLEAR_CACHE_ON_SIGN_OUT,
    SESSION_DAYS_KEY,
    VAULT_CONCEALMENT_KEY,
    session_seconds_from,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Wiring --------------------------------------------------------------------------------


def _token_from(connection: HTTPConnection) -> str:
    """The session credential this connection carries, or empty. Read one way, in one place.

    A connection rather than a request: a socket carries the same credential and is not a request.
    """
    return connection.cookies.get(SESSION_COOKIE_NAME) or ""


def _service(connection: HTTPConnection) -> AuthService:
    """The auth service, off whatever arrived holding the application.

    A connection rather than a request, because a WebSocket is not a request and carries the same
    cookie. Both are connections; only one of them has a response.
    """
    return part_of(connection, SERVICE)


# --- Dependencies other slices use ---------------------------------------------------------


class SettingsReader(Protocol):
    """The one thing session resolution needs from the preference store.

    A structural type rather than an import: the store belongs to another feature, and a slice does
    not import a slice. This names the shape that is reached for on `app.state`, so the reach is
    type-checked instead of being an untyped attribute lookup.
    """

    async def get_user(self, user_id: str, key: str) -> Any: ...


async def _concealment_for(connection: HTTPConnection, user_id: str) -> Concealment:
    """How this user has asked the vault to hide things.

    Read on every request and never held. The mode decides whether a concealed thing leaves a gap
    on the screen or leaves nothing at all, and somebody who has just changed it away from a mode
    that shows gaps is changing it because of who is about to be in the room, so it takes effect
    on the next request, not on the next sign-in.

    A stored value the registry no longer recognizes has already been turned back into the default
    by the preference store, so what arrives here is always one of the modes.
    """
    hub = part_of(connection, SETTINGS_HUB)
    return Concealment(await hub.get_user(user_id, VAULT_CONCEALMENT_KEY))


async def _vault_view(connection: HTTPConnection, viewer: Viewer, *, unlocked: bool) -> Viewer:
    """Settle both Hidden facts for this viewer: whether it is open, and how it conceals.

    Both are settled here rather than one here and one at the call site. That is the point of the
    function: they are the two things that decide what every scoped query may return, and splitting
    them is how one of them quietly stops being checked.

    **Every user gets both**, guests included, and the reason is the shape of the feature rather
    than a relaxation of it. Hiding is personal: what a viewer has hidden is concealed from that
    viewer and from nobody else. So opening it reveals only what the user asking put out of its
    own sight, and there is nothing there that belongs to anybody else. A user with no PIN can
    never reach the unlock route at all, so `unlocked` is false for them by construction.

    **The mode is theirs for the same reason.** Placeholder mode hands a viewer's own concealed rows
    back as gaps rather than absences. A row is only ever concealed from the user who hid it, so
    the preference describes their own screen and nothing else's.
    """
    return replace(
        viewer,
        show_hidden=unlocked,
        concealment=await _concealment_for(connection, viewer.id),
    )


#: What a locked session gets, and the routes that are exempt from it.
#:
#: 423 rather than 401, and the difference matters to the client: 401 means "sign in", and a browser
#: told that would clear its session and lose the very thing the PIN was going to open. 423 means
#: "this session is real and is shut", which is a screen with a PIN box on it.
LOCKED_SESSION = "Sift is locked on this session"

#: What marks a 423 as "the whole session is shut" rather than "one file is in your vault".
#:
#: TWO different refusals answer 423 and they mean opposite things to a browser. This one means the
#: session itself is closed and the only way on is the PIN, so the client leaves the page it is on
#: and loads the lock screen. `kernel.reach.vault_locked` also answers 423, for ONE named file the
#: asker's own vault is concealing, and that is an ordinary refusal of an ordinary write, with the
#: rest of the screen still working and an Unlock button on the message.
#:
#: Told apart by a header rather than by the status alone, which is exactly what the 401 above does
#: and for the same reason: a list of paths would have to be remembered in two places. THE MARK IS
#: ON THIS ONE, not on the vault's, and that direction is the whole safety of it. A refusal that
#: forgets the mark is read as the ordinary kind: a message, and the person stays where they are.
#: Marking the vault's instead would mean a new vault refusal that forgot it threw somebody out of
#: the app and asked for a PIN, which is the fault this exists to fix.
#:
#: Sift's own header rather than `WWW-Authenticate`: that one names a way to authenticate and a
#: locked session is already authenticated. This says which of the two shut things is shut.
SESSION_LOCKED_MARK = {"Sift-Locked": "session"}


@dataclass(frozen=True, slots=True)
class SessionViewer:
    """Who is behind a connection, and whether Sift is locked on that session.

    Both come out of one resolution because both are decided by it, and because what each caller
    does with the second half differs: an HTTP route refuses a locked session with a status code,
    and a socket has no status code to refuse with, so it simply does not open. Returning the two
    facts rather than raising is what lets one resolver serve both.
    """

    viewer: Viewer | None
    locked: bool


async def viewer_on(connection: HTTPConnection) -> SessionViewer:
    """The user behind this connection, rebuilt from the database.

    **The one place a session becomes a viewer**, and it takes a connection rather than a request
    so that a socket goes through it too. A second copy of this path for sockets would resolve the
    user and could silently skip the two Hidden facts, which is exactly the shape of thing a second
    copy of a security path gets wrong.

    None covers every way there is nobody: nothing to identify the session, an expired or revoked
    one, or a user who has been deleted or disabled since it was issued. Because it is
    re-resolved every time, disabling a user ends their live session on that user's very next
    request.

    The two Hidden facts are resolved here for the same reason the role is: they decide what every
    scoped query is allowed to return, so they have to be settled once, on the way in, and not left
    to each route to remember. `show_hidden` is asked of this session (not this user), because
    opening Hidden is something one browser did, and a second browser signed in as the same user
    has not done it.
    """
    token = _token_from(connection)
    if not token:
        return SessionViewer(None, locked=False)
    service = _service(connection)
    resolved = await service.resolve_session_state(token)
    if resolved is None:
        return SessionViewer(None, locked=False)
    # Built closed, then opened only as far as this session has actually opened it. The defaults on
    # a viewer are the shut ones: they are what it *is* until something says otherwise, never a
    # fallback, and the unlock is a fact about this browser, re-read every time.
    viewer = await part_of(connection, ACCESS).load_viewer(resolved.user_id)
    if viewer is None:
        return SessionViewer(None, locked=resolved.locked)
    # Hidden is shut on a locked session because locking and unlocking both clear the entry this
    # reads: the two are separate acts and the PIN opens them separately. Written there rather
    # than as a second condition here, so there is one rule about it and a test can kill it.
    unlocked = service.vault_unlocks.is_unlocked(hash_token(token))
    # Which window and device this request came from, for every writer below its route that
    # stamps a record with it (the ledger's door among them). Set here, once a session has
    # resolved to somebody, so a request nobody signed in made carries nothing.
    enter_client(client_of(connection))
    return SessionViewer(
        await _vault_view(connection, viewer, unlocked=unlocked), locked=resolved.locked
    )


async def _resolve_viewer(request: Request, *, allow_locked: bool = False) -> Viewer | None:
    """The user behind the session, rebuilt from the database, or None.

    The HTTP half of `viewer_on` above, which does the resolving. What is added here is the
    refusal: a locked session resolves to a user and is then turned away with a status code.

    **The app lock is enforced here, once, for everything.** A locked session resolves to a user
    and is then refused, so every route that asks who is calling is refused by construction: a new
    route cannot forget to check, because it never gets a viewer. `allow_locked` is the exception
    and there are three of them, each named at the route that takes it: unlocking, signing out, and
    asking who you are. Anything else, including the raw cookie replayed at the API from another
    client, gets a 423.
    """
    found = await viewer_on(request)
    if found.locked and not allow_locked:
        raise HTTPException(status.HTTP_423_LOCKED, LOCKED_SESSION, headers=SESSION_LOCKED_MARK)
    return found.viewer


#: What marks a 401 as "there is no session" rather than "the credential you just typed is wrong".
#:
#: Both are 401 and they mean opposite things to a client. A missing session should send somebody to
#: the sign-in screen; a wrong password on the change-password form should not, or mistyping your own
#: password signs you out. Telling them apart by URL means every route that checks a credential has
#: to be remembered somewhere else, and one that is forgotten fails in the worse direction.
#:
#: So the session refusal says so, in a header a client can read. `WWW-Authenticate` is the header
#: for exactly this and the scheme name is Sift's own: there is no bearer token and no basic auth
#: here, only a session cookie.
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
    """The signed-in user, or None. For a route that behaves differently signed out but does not
    require a session to answer.

    A locked session is refused here rather than read as nobody. It is not signed out: it is a
    real session that has been shut, and answering as though nobody were there would serve the
    signed-out version of the page to somebody the lock is meant to stop.
    """
    return await _resolve_viewer(request)


async def viewer_even_if_locked(request: Request) -> Viewer:
    """The signed-in user, whether or not Sift is locked on this session.

    Three routes take this and no more: unlocking, signing out, and asking who you are. Each is
    something a locked screen has to be able to do: the first is the way out, the second is the
    stronger way out, and the third is what the sign-in screen reads to know which of them to draw.
    """
    viewer = await _resolve_viewer(request, allow_locked=True)
    if viewer is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "not signed in", headers=SESSION_CHALLENGE
        )
    return viewer


async def require_admin(
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Viewer:
    """An admin, or a 403. Admin-only surfaces are enforced here, on the server: hiding the button
    in the client is not this, and is never trusted to be."""
    if not viewer.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admins only")
    return viewer


async def master_key(request: Request) -> bytes | None:
    """The unwrapped master key for the current session, or None.

    None when nobody is signed in, when the signed-in user is a guest (which holds no key), or
    after a restart before the password has been entered again. A job or route that needs a saved
    site login checks this: None means wait for a login, not fail.
    """
    viewer = await _resolve_viewer(request)
    if viewer is None:
        return None
    return _service(request).master_keys.get(viewer.id)


def session_key(request: Request) -> str | None:
    """The handle this request's session is known by in the vault-unlock store, or None.

    Published so the vault can open and close itself for *this* browser without holding the cookie
    or knowing how a session is identified. It is the hashed token, never the token, so what the
    vault feature passes around cannot be replayed as a cookie.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return None if token is None else hash_token(token)


async def require_vault_pin(request: Request, viewer: Viewer) -> None:
    """Refuse to conceal anything for a user who has no PIN yet.

    The PIN is the only thing that opens the vault, so putting something into one without a PIN set
    would hide it with no way back. A vault with no key is not a vault, and this is the server-side
    half of that rule: the screens prompt to create a PIN first, and a caller that skips the screen
    is refused here rather than trusted to have been prompted.
    """
    if not await _service(request).has_pin(viewer.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "set a PIN before using the vault \u2014 it's the only thing that opens it again",
        )


async def csrf_protect(request: Request) -> None:
    """Reject a state-changing request that does not carry the session's CSRF token in its header.

    The token is derived from the session token in the cookie, so this recomputes the expected
    value from the cookie and compares it in constant time to the header. No cookie or no header is
    a rejection: a GET never reaches here, and every state-changing route that can act on a session
    does.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if token is None or header is None or not tokens_equal(derive_csrf_token(token), header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")


# --- Helpers -------------------------------------------------------------------------------


async def _session_seconds(request: Request) -> int:
    """How long a sign-in lasts, as the installation currently says.

    Read HERE, at the sign-in, rather than held from boot: it is a stored setting, changeable on
    the Privacy screen, and a value captured when the process started would mean a change took
    effect at the next restart with nothing on the screen saying so. The environment variable that
    once set it is retired. See `kernel.config.RETIRED_VARIABLES`.

    The same number goes to the session row and to the cookie: two copies of one answer, handed
    the same value by the same caller rather than each reading a field of their own.
    """
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
    """Give this browser its device id in a cookie of its own, and answer the client it now is.

    The id the browser already carries is kept (it is the same device) and only set again, so it
    lives another 400 days; one is made where it carries none. Called at every sign-in, and by the
    read of who is signed in for a browser that has none yet. See `kernel/client.py` for why the id
    is the browser's and not the session's.
    """
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


# The instance-wide setting that decides whether a guest may keep a copy of a file. Written out
# rather than imported: `browse` (which owns the save endpoint) and `settings_hub` (which registers
# this setting) both depend on this slice, so importing the constant back would be a cycle. The
# string is the contract between the three, and it is registered in settings_hub.
_CAN_SAVE_KEY = "guests.can_save_to_device"


async def _can_save_to_device(request: Request, *, is_admin: bool) -> bool:
    """Whether this viewer may save originals to their own machine.

    Always for an admin; for a guest only when an admin has turned it on. Read fresh from settings
    on every call (the same source the save endpoint checks), so the button the client draws and
    the door the server opens can never disagree. It is a courtesy to the client, not the control.
    """
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
    """Whether the instance still needs its admin. The one thing a signed-out client may ask.

    Without it the sign-in screen cannot tell a fresh instance from a configured one, and its only
    alternative is to post a setup attempt and read the refusal, which means guessing, with a
    write, on every cold load.
    """
    return SetupStatusResponse(needs_setup=not await service.admin_exists())


@router.post("/password/check")
async def check_password(body: PasswordCheckRequest) -> PasswordCheckResponse:
    """What the policy makes of a password somebody is still typing.

    It exists because of the one rule the browser cannot mirror. Length and variety are four lines
    of Javascript; "is this one of a hundred thousand leaked passwords" is a file that ships with
    the server, and putting it in the page would be a megabyte on every load to save one request.
    Without this the meter would say "strong" about `Password123!` and the server would refuse it
    a click later, with no way for the person typing to see it coming.

    Signed out on purpose: the first screen anybody sees is the one that creates an admin, and
    there is no session to have yet. It costs a set lookup, it names no user, it writes nothing,
    and it tells a caller only what the bundled list (a public one) already says. The password
    itself is not logged, here or anywhere.
    """
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
    """Create the one admin, on first run only. A second call is a 409: the instance already has
    an owner, and that idempotency is what stops an exposed fresh instance being claimed by whoever
    reaches it first."""
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
    """Sign in. The failure reply is the same whether the username is unknown or the password is
    wrong, and takes the same time, so it cannot be read to learn which usernames exist.

    There is no lockout here: a run of failures on a name is answered by making the next attempt on
    it slower (the service tarpits it), never by refusing a correct password, so the one admin
    cannot be shut out of their own instance by someone guessing at the login. The one 429 is for a
    second sign-in on the same username from the same address while the first is still being
    checked, with a `Retry-After`."""
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
    """Sign out. The session is revoked in the database, so the cookie that is being cleared is
    already dead even if the client keeps a copy of it.

    Reachable from a locked session, and it has to be: signing out is the stronger of the two ways
    back and the one somebody reaches for when they have forgotten the PIN."""
    # `current_viewer` already resolved a session, so the handle is there. Written without a branch
    # for the case where it is not, because there is no such case to reach and an unreachable
    # branch is one nobody can prove behaves: revoking a handle nobody holds is already a no-op in
    # the service, so the empty string is the same instruction.
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
    """Who the caller is, and the CSRF token to send with state-changing requests.

    The username is read back from the user's row rather than trusted from anything the client
    holds. The CSRF token is recomputed from the session cookie, so a page loads it once and echoes
    it thereafter.

    Answered from a locked session, and it says so: without it the lock screen could not tell whose
    PIN it was asking for, and unlocking needs the CSRF token this hands back. It carries nothing
    about the library: a name, a role and a token bound to a cookie the caller already holds.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    # current_viewer resolved a session, so the cookie is present.
    csrf = derive_csrf_token(token) if token is not None else ""
    # A browser signed in before devices were kept has none yet: it is given one here, the first
    # time it asks who it is, and its session row learns it.
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
        # Offered only where the PIN route would accept it, so the lock screen never draws a
        # PIN box that is then refused.
        pin_unlock_offered=_over_the_local_network(request)
        and await service.may_reopen_with_pin(viewer.id),
        locked=await service.is_locked(_token_from(request)),
        # Admins only, as `/health` gives it: the unlock bar that reads it is an admin's.
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
    """Put this user's master key back in memory. The session is untouched.

    Nothing new is authorized by it: the caller already holds a valid session, and the password is
    the same one that would unwrap the key at sign-in. What it avoids is signing somebody out of a
    working session to recover a key the process dropped when it restarted.
    """
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
    """Change your own password. Every user may, guests included; the old password is required.
    For a user who holds a master key it is re-wrapped, so saved logins survive. Every other
    session is revoked; this one is kept, so the change does not sign you out of your own browser."""
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
    """Set or change the PIN that unlocks a locked screen. The current password is required, so an
    unattended unlocked screen cannot be used to plant one."""
    try:
        await service.set_pin(viewer.id, body.pin, body.current_password)
    except InvalidCredentials as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "password is incorrect") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- User management (admin) -----------------------------------------------------------------
#
# Every route below is an admin route, and every one of them acts on somebody else. That is the
# shape of the whole section: an admin manages guests here, and manages their own sign-in from
# their profile. It is why none of these needs a "not yourself" check: the service refuses any
# user who is not a guest, and the only admin is the one asking.


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
    """Every user on this instance.

    Admin-only, and the list itself is the reason rather than what can be done from it: who has a
    sign-in here is a fact about the household, and a guest asking who else was let in is asking
    something that is not theirs to know.
    """
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
    """Add a guest with a first password an admin chooses.

    Creating an admin is not offered, here or anywhere: the one admin is made once, by first-run
    setup, and a second one is a decision with consequences for what each can conceal from the
    other. This route makes guests, and the role is not a parameter it accepts.
    """
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
    """Add a guest without deciding anything: Sift invents the name and the password.

    **The password is in this reply and nowhere else.** It is not stored in the clear, not returned
    by any later read, and not logged, so it is shown once, the screen says so, and losing it
    means resetting the password rather than looking it up. That is the whole reason this is a
    separate route from the one that takes a password: a route that could hand a password back
    would have to be able to read one.

    Both halves come from the operating system's random source. There is no expiry and no cleanup:
    this makes an ordinary guest user that behaves like every other one, and a throwaway variant
    would need rules about when it goes away that nobody has decided.
    """
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
    """Change what a user is called: your own always, anybody's if you are an admin.

    The one route in this section that is not admin-only, and the one that acts on the caller. The
    guard is written here rather than taken as a dependency because it is neither of the two shapes
    a dependency offers: it is "you, or an admin".

    Nothing is signed out. A name is a label, not a credential, and the user keeps their id, so
    every share, rating and hidden row they have is untouched, and the saved site logins still open
    because the key was never tied to the name.
    """
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
    """Stop a user signing in, or let it again.

    It takes effect on the user's very next request, not at their next login: the flag is read
    from the row every time anybody resolves who is asking. The sessions they already have are
    dropped as well, so turning them back on does not silently restore a browser left open.
    """
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
    """Set a guest's password without knowing their old one, and end their sessions.

    Only a guest, which is what makes it safe to do without the old password: a guest holds no
    wrapped key, so nothing is locked under the password being replaced. The same operation on an
    admin would throw away their saved site logins, and the console tool that can do it says so
    before it does.
    """
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
    """Remove a guest user, their sessions, and every share and restrict made to it.

    All three go together by foreign key. A grant left behind naming a deleted user would be
    inert today and dangerous the moment an id is reused, and this is the one side of the access
    model where the database can be trusted to do it: the subject of a grant really is a row in
    the `users` table.
    """
    try:
        await service.delete_user(admin, user_id)
    except NoSuchUser as exc:
        raise _user_gone() from exc
    except NotAGuestUser as exc:
        raise _not_a_guest() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the app lock ------------------------------------------------------------------------------

#: Why the PIN was refused, for a request that did not come from the local network. The lock screen
#: shows it as it switches to the password, which always works.
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

    The mark goes on the session row, so every path to the server is shut at once: another tab, a
    reload, the credential replayed at the API by hand. A lock drawn over the screen leaves all
    three working, and that is the difference this route exists to make.

    **Which way it shuts is decided here, not by the caller.** Whether a PIN may reopen a session
    is a setting this server holds and a PIN it stores. A client deciding it has to read both
    first, and asking before an answer had landed it would read "no PIN" for somebody who had one
    and sign them out, intermittently, which is the hardest kind of fault to be believed about.
    The answer names what happened so the screen knows where to send somebody, rather than
    working it out a second time.

    Offered to everybody, guests included, and **allowed on a session that is already locked**,
    which is the fourth route that has to work while shut, alongside the two unlocks and signing
    out. A panic control pressed twice must not be worse than pressed once, and every other route
    refuses a locked session, so without the exception the second press would answer 423: the
    client would read that as "shut" and bounce, which looks like the shortcut needing two presses
    and, once the session had been reopened and shut again, like being signed out.

    Locking never fails for the caller: a session that has already gone is already shut, and one
    already locked is locked again to the same effect.
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
    """Open a locked session with the user's password.

    Always available, and that is what makes locking safe to be the only shape. Locking shuts a
    session rather than throwing it away, so the thing that has always opened one still does,
    and somebody who never set a PIN, or turned that option off, types a password rather than a
    username and a password.

    It opens a session that exists and never mints one, exactly as the PIN route does: a browser
    holding nothing has nothing here to unlock and is sent to the front door.

    Hidden stays shut. The same secret opens two different things and it opens them one at a time.
    """
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
    """Open a locked session with the PIN.

    It verifies and clears the lock in one step. There is no route that answers "is this the PIN"
    without doing anything, because a question with no side effect is an oracle: a caller could ask
    it as often as the throttle allowed and learn the secret without ever having to spend a session.

    It unlocks a session that exists and never mints one, which is what stops the PIN becoming a
    weaker way in. A browser with no session has nothing here to unlock and is asked for the
    password.

    Hidden stays shut. The same secret opens two different things and it opens them one at a time:
    coming back to the app does not come back to a screenful of hidden files.

    A run of wrong PINs destroys the session, and the caller is at the password. The refusal is the
    same 401 either way: saying "and that was your last one" would tell somebody guessing exactly
    how much room they had left.

    Only from the local network. A request that came through a tunnel or a proxy, or from an
    address outside the private ranges, is refused with a 403 before the PIN is read, so it is
    neither verified nor counted, and the screen switches to the password.
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
