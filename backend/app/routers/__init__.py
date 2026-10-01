from fastapi import APIRouter

from .core import router as core_router
from .exams import router as exams_router
from .profiles import router as profiles_router
from .workspace import router as workspace_router, scoped_router as scoped_workspace_router
from .terms import router as terms_router
from .attachments import router as attachments_router
from .agent import router as agent_router
from .plugin import router as plugin_router
from .plugins import router as plugins_router
from .vision import router as vision_router
from .documents import router as documents_router
from .school_sync import router as school_sync_router
from .token_usage import router as token_usage_router
from .growth import router as growth_router

from .teaching import router as teaching_router
from .practice import router as practice_router

router = APIRouter()
router.include_router(teaching_router)
router.include_router(practice_router)
router.include_router(core_router)
router.include_router(exams_router)
router.include_router(profiles_router)
router.include_router(workspace_router)
router.include_router(scoped_workspace_router)
router.include_router(terms_router)
router.include_router(attachments_router)
router.include_router(agent_router)
router.include_router(plugin_router)
router.include_router(plugins_router)
router.include_router(vision_router)
router.include_router(documents_router)
router.include_router(school_sync_router)
router.include_router(token_usage_router)
router.include_router(growth_router)

__all__ = ["router"]
