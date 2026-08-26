from app.models.usage_store import ModelUsageStore, model_usage_scope
from app.settings import Settings


def test_model_usage_ledger_uses_configured_pricing_and_tenant_scope(tmp_path):
    settings = Settings(
        database_path=tmp_path / "unihr.db",
        model_pricing_json='{"qwen-test":{"input_per_million":2,"output_per_million":4}}',
    )
    store = ModelUsageStore(settings)
    with model_usage_scope("tenant_a", "user_a", "run_a"):
        store.record(
            provider="bailian",
            model="qwen-test",
            purpose="document_plan",
            operation_kind="chat",
            input_units=1_000_000,
            output_units=500_000,
            unit_type="tokens",
        )

    summary = store.tenant_summary("tenant_a")
    other = store.tenant_summary("tenant_b")

    assert summary["calls"] == 1
    assert summary["estimated_cost_cny"] == 4
    assert summary["pricing_configured"] is True
    assert summary["breakdown"][0]["purpose"] == "document_plan"
    assert other["calls"] == 0


def test_model_usage_can_bind_async_execution_and_raise_budget_alerts(tmp_path):
    settings = Settings(
        database_path=tmp_path / "unihr.db",
        model_pricing_json='{"qwen-test":{"input_per_million":2,"output_per_million":4}}',
    )
    store = ModelUsageStore(settings)
    with model_usage_scope("tenant_a", "user_a", "exec_a"):
        store.record(
            provider="bailian", model="qwen-test", purpose="route",
            operation_kind="chat", input_units=1_000_000, output_units=0,
        )
    assert store.bind_execution(
        "tenant_a", "exec_a", conversation_id="conversation_a", workflow_run_id="run_a"
    ) == 1
    store.update_budget_policy(
        "tenant_a", monthly_budget_cny=1, per_run_budget_cny=1,
        daily_call_limit=1, warning_ratio=0.8, updated_by="admin_a",
    )

    control = store.cost_control("tenant_a")
    summary = store.tenant_summary("tenant_a")

    assert control["monthly_cost_cny"] == 2
    assert {item["code"] for item in control["alerts"]} >= {
        "monthly_budget_exceeded", "daily_call_limit", "per_run_budget",
    }
    assert summary["pricing_coverage"] == 1
    assert summary["attribution"][0]["conversation_id"] == "conversation_a"
