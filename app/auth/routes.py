"""Local authentication: routes."""
from __future__ import annotations

import secrets
import uuid

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .bootstrap import (
    _bootstrap_credentials_lock,
    _read_bootstrap_accounts,
    _write_bootstrap_accounts,
)
from .constants import (
    _USERNAME_RE,
    ANONYMOUS_TTL_SECONDS,
    CSRF_FIELD,
    MIN_PASSWORD_LENGTH,
    ROLE_ADMIN,
    ROLE_USER,
    ROLES,
    SESSION_TTL_SECONDS,
    log,
    router,
)
from .credentials import _verify_dummy_password, verify_password
from .rate_limit import _clear_failures, _failure_key, _rate_limit_wait, _record_failure
from .security import current_user, require_admin, require_user, safe_next
from .sessions import (
    _clear_session_cookie,
    _create_session,
    _revoke_other_sessions,
    _revoke_session,
    _revoke_sessions_for,
    _rotate_csrf,
    _session_row,
    _set_session_cookie,
)
from .users import (
    _deactivate_without_losing_last_admin,
    _get_user_by_id,
    _get_user_by_username,
    _insert_user,
    _mark_login,
    _set_active,
    _set_password,
)
from .views import (
    _account_response,
    _login_response,
    _password_problem,
    _start_anonymous_session,
    _users_response,
)


def _default_home(user) -> str:
    return "/admin" if user and user["role"] == ROLE_ADMIN else "/"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request) is not None:
        return RedirectResponse(
            url=safe_next(request.query_params.get("next")) or _default_home(current_user(request)),
            status_code=303,
        )
    target = safe_next(request.query_params.get("next"))
    token, _ = _start_anonymous_session(request)
    response = _login_response(request, next_url=target)
    if token:
        _set_session_cookie(response, token, ANONYMOUS_TTL_SECONDS)
    return response



@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf: str = Form("", alias=CSRF_FIELD),
    next_url: str = Form("", alias="next"),
):
    session_id = getattr(request.state, "auth_session_id", None)
    session = _session_row(session_id) if session_id else None
    target = safe_next(next_url)
    if current_user(request) is not None:
        return RedirectResponse(url=target or _default_home(current_user(request)), status_code=303)

    # CSRF first: credentials are only inspected with a token bound to this session.
    if (
        session is None
        or not csrf
        or not secrets.compare_digest(csrf.encode(), session["csrf_token"].encode())
    ):
        token, _ = _start_anonymous_session(request)
        response = _login_response(
            request,
            error="Je sessie is verlopen. Probeer het opnieuw.",
            username=username,
            next_url=target,
            status_code=403,
        )
        if token:
            _set_session_cookie(response, token, ANONYMOUS_TTL_SECONDS)
        return response

    key = _failure_key(request, username)
    wait = _rate_limit_wait(key)
    if wait:
        return _login_response(
            request,
            error="Te veel mislukte pogingen. Probeer het straks opnieuw.",
            username=username,
            next_url=target,
            status_code=429,
            headers={"Retry-After": str(int(wait) + 1)},
        )

    row = _get_user_by_username(username)
    if row is None:
        _verify_dummy_password(password)
    valid_password = verify_password(password, row["password_hash"]) if row is not None else False
    if row is None or not row["active"] or not valid_password:
        _record_failure(key)
        log.warning("login failed for user=%r", username)
        csrf = _rotate_csrf(session["token_hash"]) or session["csrf_token"]
        request.state.csrf_token = csrf
        return _login_response(
            request,
            error="Onjuiste gebruikersnaam of wachtwoord.",
            username=username,
            next_url=target,
            status_code=401,
        )

    _clear_failures(key)
    _revoke_session(session["token_hash"])
    token, csrf = _create_session(row["id"], SESSION_TTL_SECONDS, request.headers.get("user-agent"))
    _mark_login(row["id"])
    with _bootstrap_credentials_lock:
        accounts = _read_bootstrap_accounts()
        if isinstance(accounts.get(row["username"]), dict) and accounts[row["username"]].get(
            "password"
        ):
            accounts[row["username"]]["password"] = None
            _write_bootstrap_accounts(accounts)
    response = RedirectResponse(url=target or _default_home(row), status_code=303)
    _set_session_cookie(response, token, SESSION_TTL_SECONDS)
    log.info("login ok for user id=%s", row["id"])
    return response



@router.post("/logout", dependencies=[Depends(require_user)])
def logout(request: Request):
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id:
        _revoke_session(session_id)
    response = RedirectResponse(url="/login", status_code=303)
    _clear_session_cookie(response)
    return response



@router.get("/account", response_class=HTMLResponse, dependencies=[Depends(require_user)])
def account_page(request: Request):
    return _account_response(request, user=require_user(request))



@router.post("/account/password", response_class=HTMLResponse, dependencies=[Depends(require_user)])
def account_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    user = require_user(request)
    row = _get_user_by_id(user["id"])
    if row is None:
        raise HTTPException(status_code=401, detail="Niet ingelogd.")
    if not verify_password(current_password, row["password_hash"]):
        return _account_response(
            request, user=user, error="Het huidige wachtwoord klopt niet.", status_code=400
        )
    problem = _password_problem(new_password, confirm_password, row["password_hash"])
    if problem:
        return _account_response(request, user=user, error=problem, status_code=400)
    _set_password(row["id"], new_password)
    revoked = _revoke_other_sessions(row["id"], getattr(request.state, "auth_session_id", None))
    log.info("password changed for user id=%s, %d other sessions revoked", row["id"], revoked)
    return RedirectResponse(url="/account?changed=1", status_code=303)



@router.get("/users", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def users_page(request: Request):
    return _users_response(request, user=require_admin(request))



@router.post("/users", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def users_create(
    request: Request,
    username: str = Form(""),
    display_name: str = Form(""),
    role: str = Form(ROLE_USER),
    password: str = Form(""),
):
    admin = require_admin(request)
    candidate = username.strip()
    if not _USERNAME_RE.fullmatch(candidate):
        return _users_response(
            request,
            user=admin,
            error="Gebruik 3–32 tekens: letters, cijfers, punt, streepje of liggend streepje.",
            status_code=400,
        )
    if role not in ROLES:
        return _users_response(request, user=admin, error="Onbekende rol.", status_code=400)
    if _get_user_by_username(candidate) is not None:
        return _users_response(
            request, user=admin, error="Deze gebruikersnaam bestaat al.", status_code=400
        )

    generated = ""
    if password:
        if len(password) < MIN_PASSWORD_LENGTH:
            return _users_response(
                request,
                user=admin,
                error=f"Kies een wachtwoord van minstens {MIN_PASSWORD_LENGTH} tekens.",
                status_code=400,
            )
        chosen = password
    else:
        chosen = secrets.token_urlsafe(18)
        generated = chosen

    user_id = str(uuid.uuid4())
    _insert_user(user_id, candidate, display_name.strip() or candidate, role, chosen)
    log.info("user created id=%s role=%s by admin id=%s", user_id, role, admin["id"])
    return _users_response(
        request,
        user=admin,
        notice=f"Account {candidate} aangemaakt.",
        new_password=generated,
        created_username=candidate,
    )



@router.post("/users/{user_id}/deactivate", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def users_deactivate(request: Request, user_id: str):
    admin = require_admin(request)
    target = _get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    if target["id"] == admin["id"]:
        return _users_response(
            request, user=admin, error="Je kunt je eigen account niet deactiveren.", status_code=400
        )
    outcome = _deactivate_without_losing_last_admin(target["id"])
    if outcome == "last_admin":
        return _users_response(
            request,
            user=admin,
            error="Er moet altijd minstens één actieve beheerder blijven.",
            status_code=400,
        )
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    revoked = _revoke_sessions_for(target["id"])
    log.info("user deactivated id=%s, %d sessions revoked", target["id"], revoked)
    return _users_response(request, user=admin, notice=f"{target['username']} is gedeactiveerd.")



@router.post("/users/{user_id}/activate", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def users_activate(request: Request, user_id: str):
    admin = require_admin(request)
    target = _get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    _set_active(target["id"], True)
    log.info("user activated id=%s", target["id"])
    return _users_response(request, user=admin, notice=f"{target['username']} is weer actief.")



@router.post("/users/{user_id}/reset-password", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def users_reset_password(request: Request, user_id: str):
    admin = require_admin(request)
    target = _get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Onbekende gebruiker.")
    password = secrets.token_urlsafe(18)
    _set_password(target["id"], password)
    revoked = _revoke_sessions_for(target["id"])
    log.info("password reset for user id=%s, %d sessions revoked", target["id"], revoked)
    return _users_response(
        request,
        user=admin,
        notice=f"Nieuw wachtwoord voor {target['username']}. Alle sessies zijn afgemeld.",
        new_password=password,
        created_username=target["username"],
    )

