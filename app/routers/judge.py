"""Five for today and the reason behind a "nee": quick answers that make the list sharper."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from .. import judgements, store
from ..auth import require_user
from ..web_helpers import _jobs_return_target, _with_done

router = APIRouter(dependencies=[Depends(require_user)])


@router.post("/vandaag/oordeel/{job_id}")
def judge_answer(job_id: int, answer: str = Form(...)):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    try:
        judgements.answer(job_id, answer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown answer") from exc
    return RedirectResponse(url="/#vijf", status_code=303)


@router.post("/jobs/{job_id}/reden")
def judge_reason(job_id: int, reason: str = Form(...), url: str = Form("/", alias="next")):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if not judgements.set_reason(job_id, reason):
        raise HTTPException(status_code=400, detail="unknown reason")
    target = _jobs_return_target(url)
    return RedirectResponse(url=target + "#vijf" if target == "/" else _with_done(target, job_id, "reden"), status_code=303)
