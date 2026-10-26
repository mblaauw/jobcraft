"""routers interview responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field

from .. import (
    config,
    interview_state,
    llm,
    profile,
    store,
)
from ..auth import require_user
from ..runtime import _session_lock, get_manager
from ..util import now_local
from ..web import templates
from ..web_helpers import _last_assistant_text, _persist_turn, _visible_messages

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/interview", response_class=HTMLResponse)
def interview_page(request: Request):
    session_id = request.query_params.get("session")
    if session_id and not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="unknown session")
    sessions = store.list_sessions(30)
    messages = _visible_messages(session_id) if session_id else []
    return templates.TemplateResponse(
        request,
        "interview.html",
        {
            "sessions": sessions,
            "session_id": session_id,
            "messages": messages,
            "coverage": interview_state.summary(),
        },
    )


@router.post("/interview/toggle")
def interview_toggle(
    topic_id: str = Form(...), status: str = Form(...), session_id: str = Form("")
):
    try:
        interview_state.toggle(topic_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = f"/interview?session={session_id}" if session_id else "/interview"
    return RedirectResponse(url=target, status_code=303)


@router.post("/interview/new")
def interview_new():
    mgr = get_manager()
    session_id = mgr.create_session(agent=config.AGENT_INTERVIEWER)
    title = f"Interview {now_local().strftime('%d-%m %H:%M')}"
    store.create_session(session_id, title, config.AGENT_INTERVIEWER, config.MODEL_LABEL)
    try:
        result = mgr.run_turn(session_id, config.KICKOFF_TEXT)
        _persist_turn(session_id, result, "interview", hide_kickoff=True)
    except llm.LLMError as exc:
        store.update_session(session_id, title=title + " (fout)")
        log.warning("kickoff failed: %s", exc)
    return RedirectResponse(url=f"/interview?session={session_id}", status_code=303)


class SendBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.get("/api/sessions")
def api_sessions():
    return {"sessions": [dict(row) for row in store.list_sessions(30)]}


@router.get("/api/sessions/{session_id}/messages")
def api_messages(session_id: str):
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="unknown session")
    return {"messages": _visible_messages(session_id)}


@router.post("/api/sessions/{session_id}/send")
def api_send(session_id: str, body: SendBody):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="unknown session")
    mgr = get_manager()
    lock = _session_lock(session_id)
    with lock:
        question = _last_assistant_text(session_id)
        try:
            result = mgr.run_turn(session_id, body.text.strip())
        except llm.LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        _persist_turn(session_id, result, "interview")
        profile.append_answer(question, body.text.strip())
        interview_state.record_answer(question, body.text.strip())
    return {"messages": _visible_messages(session_id)}


@router.post("/api/sessions/{session_id}/send/stream")
def api_send_stream(session_id: str, body: SendBody):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="unknown session")
    mgr = get_manager()
    lock = _session_lock(session_id)

    def generate():
        with lock:
            question = _last_assistant_text(session_id)
            try:
                for event in mgr.run_turn_stream(session_id, body.text.strip()):
                    if event["type"] == "token":
                        yield (
                            "data: " + json.dumps({"type": "token", "text": event["text"]}) + "\n\n"
                        )
                    elif event["type"] == "error":
                        yield (
                            "data: "
                            + json.dumps({"type": "error", "detail": event["detail"]})
                            + "\n\n"
                        )
                        return
                    elif event["type"] == "done":
                        result = {
                            "messages": [event["assistant"]],
                            "usage": event.get("usage", {}),
                            "session": {},
                        }
                        _persist_turn(session_id, result, "interview")
                        profile.append_answer(question, body.text.strip())
                        interview_state.record_answer(question, body.text.strip())
                        yield (
                            "data: "
                            + json.dumps({"type": "done", "messages": [event["assistant"]]})
                            + "\n\n"
                        )
            except Exception as exc:  # pragma: no cover - defensive
                yield "data: " + json.dumps({"type": "error", "detail": str(exc)}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
