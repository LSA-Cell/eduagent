"""学习计划失败信息不得向客户端泄露内部诊断。"""

from app.schemas import PlanCreateRequest
from app.services import plan_service
from tests.conftest import FakeLLM, FakeSession, TEST_USER_ID


async def test_plan_failure_keeps_diagnostics_private(monkeypatch):
    logged = []
    monkeypatch.setattr(plan_service.log, "warning", lambda *args, **kwargs: logged.append((args, kwargs)))
    session = FakeSession()
    plan = await plan_service.create_plan(
        session, TEST_USER_ID,
        PlanCreateRequest(goal_description="复习数学", current_level="初一", days=3),
    )
    internal_error = "upstream http://gateway:8001/v1/chat/completions key=private-test-value"
    result = await plan_service.run_plan_generation(
        session, FakeLLM(json_result=RuntimeError(internal_error)), plan,
    )
    assert result["status"] == "failed"
    assert plan.plan_content["_error"] == "学习计划生成失败，请稍后重试"
    response = plan_service.plan_to_dict(plan)
    assert response["error"] == "学习计划生成失败，请稍后重试"
    assert "gateway:8001" not in str(response)
    assert "private-test-value" not in str(response)
    assert "private-test-value" not in str(logged)
    assert "gateway:8001" not in str(logged)
    # 兼容历史记录：旧的原始诊断也不能由 DTO 透出。
    plan.plan_content = {"_error": internal_error}
    assert plan_service.plan_to_dict(plan)["error"] == "学习计划生成失败，请稍后重试"
