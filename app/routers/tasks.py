"""routers tasks responsibilities extracted without route changes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import (
    auth,
    tasks,
)
from ..auth import require_user

log = logging.getLogger("jobcraft")

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/api/tasks")
def api_tasks(request: Request):
    user = auth.require_user(request)
    return {
        "tasks": [
            {
                "id": task["id"],
                "kind": task["kind"],
                "status": task["status"],
                "error": task["error"],
            }
            for task in tasks.list_for_owner(user["id"])
        ]
    }


@router.get("/api/tasks/{task_id}")
def api_task(task_id: int, request: Request):
    user = auth.require_user(request)
    task = tasks.get(task_id, owner=user["id"])
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    result = None
    if task.get("result_json"):
        try:
            result = json.loads(task["result_json"])
        except ValueError:
            result = None
    return {
        "id": task["id"],
        "kind": task["kind"],
        "status": task["status"],
        "error": task["error"],
        "result": result,
    }
