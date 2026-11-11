"""Admin: which model does what, and what every AI call received and returned."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import ai_log, auth, config, model_routes, runtime, store, tenancy
from ..auth.users import _list_users
from ..llm_client import LLMError
from ..web import templates

router = APIRouter(dependencies=[Depends(auth.require_admin)])


def readable(call: dict, viewer: str) -> dict:
    """Prompts hold someone's CV text: an admin reads their own here, others only on the server."""
    if call.get("owner") == viewer:
        return call
    return {key: value for key, value in call.items() if key not in ("input", "output", "ref", "error")}


def _account_names() -> dict[str, str]:
    return {row["id"]: row["username"] for row in _list_users()}


@router.get("/admin/ai", response_class=HTMLResponse)
def ai_page(request: Request):
    agent = request.query_params.get("taak", "")
    owner = request.query_params.get("account", "")
    model = request.query_params.get("model", "")
    names = _account_names()
    if owner and owner not in names:
        owner = ""
    chosen = model_routes.routes()
    thinking = model_routes.reasoning_routes()
    overview = store.usage_overview()
    roles = {
        role: {
            "id": role,
            "label": label,
            "about": about,
            "chosen": chosen.get(role, ""),
            "default": config.role_model(role),
            "reasoning": thinking.get(role, ""),
            # Only a default set in the environment is worth naming; otherwise the model decides.
            "reasoning_default": model_routes.REASONING.get(config.role_reasoning(role) or "-", ""),
        }
        for role, label, about in model_routes.ROLES
    }
    return templates.TemplateResponse(
        request,
        "admin_ai.html",
        {
            "overview": overview,
            # Many gateways report no price: then dollars say nothing and tokens are the brake.
            "priced": any(row["cost"] for row in overview["kinds"]),
            "token_limit": int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "500000") or 0),
            "stages": [{"label": label, "roles": [roles[role] for role in ids]} for label, ids in model_routes.STAGES],
            "available": model_routes.available_models(),
            "reasoning_options": model_routes.REASONING,
            "calls": ai_log.recent(150, agent=agent, owner=owner, model=model),
            "labels": {role: label for role, label, _about in model_routes.ROLES} | {"systemone": "Snelle beoordeling (Jev)"},
            "names": names,
            "retention": ai_log._retention_days(),
            "filter_agent": agent,
            "filter_owner": owner,
            "filter_model": model,
            "notice": request.query_params.get("melding", ""),
            "failed": request.query_params.get("fout", ""),
        },
    )


@router.post("/admin/ai/modellen")
async def save_models(request: Request):
    form = await request.form()
    thinking = {role: str(form.get(f"reasoning_{role}") or "").strip() for role, _label, _about in model_routes.ROLES}
    if any(value not in model_routes.REASONING for value in thinking.values()):
        return RedirectResponse(url="/admin/ai?fout=Kies+een+van+de+aangeboden+denkstanden.", status_code=303)
    try:
        model_routes.save({role: str(form.get(f"model_{role}") or "") for role, _label, _about in model_routes.ROLES})
    except ValueError:
        return RedirectResponse(url="/admin/ai?fout=Een+modelnaam+bevat+ongeldige+tekens.", status_code=303)
    model_routes.save_reasoning(thinking)
    return RedirectResponse(url="/admin/ai?melding=Modelkeuze+opgeslagen.", status_code=303)


@router.post("/admin/ai/test")
def test_model(role: str = Form("")):
    """One tiny call, so a typo or a closed model shows here and not on someone's application."""
    if role not in {item[0] for item in model_routes.ROLES}:
        raise HTTPException(status_code=400, detail="unknown task")
    manager = runtime.get_manager()
    model = model_routes.model_for(role)
    started = time.monotonic()
    try:
        manager._logged_complete(
            role,
            [{"role": "user", "content": "Antwoord met alleen het woord OK."}],
            model=model,
            ref="admin-test",
        )
    except (LLMError, AttributeError) as exc:
        return RedirectResponse(
            url=f"/admin/ai?fout={model}+werkt+niet:+{type(exc).__name__}", status_code=303
        )
    seconds = time.monotonic() - started
    return RedirectResponse(url=f"/admin/ai?melding={model}+werkt+({seconds:.1f}+s).", status_code=303)


@router.get("/admin/ai/log/{owner}/{day}/{offset}", response_class=HTMLResponse)
def ai_call(request: Request, owner: str, day: str, offset: int):
    call = ai_log.read(owner, day, offset)
    if not call:
        raise HTTPException(status_code=404, detail="unknown call")
    return templates.TemplateResponse(
        request,
        "admin_ai_call.html",
        {
            "call": readable(call, tenancy.user_id()),
            "own": owner == tenancy.user_id(),
            "day": day,
            "account": _account_names().get(owner, owner),
            "label": {role: label for role, label, _about in model_routes.ROLES}.get(call.get("agent"), call.get("agent")),
        },
    )
