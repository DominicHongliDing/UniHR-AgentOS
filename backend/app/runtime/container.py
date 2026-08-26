from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.domains.conversations.repository import ConversationRepository
from app.domains.conversations.lifecycle import ConversationLifecycleService
from app.domains.context_lifecycle import ContextOperationsModule, ContextRetentionService
from app.domains.identity import IdentityRepository
from app.domains.knowledge import (
    KnowledgeDocumentExtractor,
    KnowledgeVectorIndex,
    PolicyEvaluationRepository,
)
from app.domains.files.legacy_office import LegacyOfficeConverter
from app.domains.vision import VisionEvidenceExtractor
from app.domains.recruitment import RecruitmentCaseModule
from app.domains.recruitment import RecruitmentEvidencePackageModule
from app.domains.artifacts.excel_verification import ExcelAutomationVerifier
from app.domains.artifacts.harness import ArtifactHarness
from app.domains.documents.word_verification import WordAutomationVerifier
from app.domains.runs import RunRepository
from app.domains.run_approvals import RunApprovalService
from app.domains.skills import SkillDryRunModule, SkillRegistry, SkillService
from app.domains.turns import TurnExecutionModule, TurnExecutionRepository
from app.domains.workplace_connectors import (
    FeishuConnectionModule,
    FeishuConnectionRepository,
    FeishuWorkplaceConnector,
    WorkplaceConnectorModule,
    WorkplaceConnectorRepository,
)
from app.domains.workbench import WorkbenchService
from app.domains.channel_gateway import (
    ChannelGateway,
    ChannelGatewayRepository,
    FeishuEventDecoder,
    FeishuExternalWebhookTransport,
    FeishuResourceLoader,
    FeishuTextTransport,
    RoutedFeishuTransport,
)
from app.domains.workplace_connectors.feishu_bot import FeishuBotClient, FeishuBotConfig
from app.domains.quality_operations import (
    EvaluationSnapshot,
    QualityBudgetPolicy,
    QualityOperations,
    QualityRepository,
    WorkbenchReplayAdapter,
)
from app.domains.assistant.capability_registry import default_task_capability_registry
from app.models.embeddings import BailianEmbeddingGateway
from app.models.gateway import BailianModelGateway
from app.models.usage_store import ModelUsageStore
from app.settings import Settings


@lru_cache
def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_directories()
    return settings


@lru_cache
def get_repository() -> ConversationRepository:
    settings = get_settings()
    assert settings.database_path is not None
    get_identity()
    return ConversationRepository(
        settings.database_path,
        default_tenant_id=settings.default_tenant_id,
    )


@lru_cache
def get_identity() -> IdentityRepository:
    settings = get_settings()
    assert settings.database_path is not None
    return IdentityRepository(settings.database_path, settings)


@lru_cache
def get_knowledge_index() -> KnowledgeVectorIndex:
    settings = get_settings()
    assert settings.database_path is not None
    return KnowledgeVectorIndex(
        settings.database_path,
        BailianEmbeddingGateway(settings),
        default_tenant_id=settings.default_tenant_id,
        document_extractor=KnowledgeDocumentExtractor(
            vision=VisionEvidenceExtractor(
                BailianModelGateway(settings), model=settings.ocr_model,
                localization_model=settings.vision_model,
            ),
            legacy_converter=LegacyOfficeConverter(settings.data_dir / "uploads" / "converted"),
        ),
    )


@lru_cache
def get_policy_evaluations() -> PolicyEvaluationRepository:
    settings = get_settings()
    assert settings.database_path is not None
    return PolicyEvaluationRepository(settings.database_path)


@lru_cache
def get_skill_registry() -> SkillRegistry:
    settings = get_settings()
    assert settings.database_path is not None
    return SkillRegistry(settings.database_path, settings.default_tenant_id)


@lru_cache
def get_run_repository() -> RunRepository:
    settings = get_settings()
    assert settings.database_path is not None
    get_repository()
    return RunRepository(settings.database_path)


@lru_cache
def get_recruitment_cases() -> RecruitmentCaseModule:
    settings = get_settings()
    assert settings.database_path is not None
    get_repository()
    get_run_repository()
    return RecruitmentCaseModule(settings.database_path)


@lru_cache
def get_recruitment_packages() -> RecruitmentEvidencePackageModule:
    settings = get_settings()
    assert settings.database_path is not None
    excel_verifier = (
        ExcelAutomationVerifier(
            settings.data_dir / "artifacts" / "previews",
            excel_executable=settings.excel_executable,
            timeout_seconds=settings.excel_automation_timeout_seconds,
        )
        if settings.excel_automation_enabled else None
    )
    word_verifier = (
        WordAutomationVerifier(
            settings.data_dir / "artifacts" / "previews",
            word_executable=settings.word_executable,
            timeout_seconds=settings.word_automation_timeout_seconds,
        )
        if settings.word_automation_enabled else None
    )
    return RecruitmentEvidencePackageModule(
        settings.database_path,
        settings.data_dir / "artifacts" / "recruitment-packages",
        get_recruitment_cases(),
        get_repository(),
        artifact_harness=ArtifactHarness(excel_verifier=excel_verifier),
        word_verifier=word_verifier,
    )


@lru_cache
def get_skill_service() -> SkillService:
    settings = get_settings()
    return SkillService(
        get_skill_registry(), get_repository(), BailianModelGateway(settings), settings.primary_model,
        workflow_checkpoint_dsn=settings.workflow_checkpoint_dsn,
    )


@lru_cache
def get_skill_dry_runs() -> SkillDryRunModule:
    settings = get_settings()
    return SkillDryRunModule(
        settings,
        get_skill_registry(),
        BailianModelGateway(settings),
        knowledge_index=get_knowledge_index(),
    )


@lru_cache
def get_workbench() -> WorkbenchService:
    return WorkbenchService(
        get_settings(), get_repository(), knowledge_index=get_knowledge_index(), skill_registry=get_skill_registry(),
    )


@lru_cache
def get_context_operations() -> ContextOperationsModule:
    settings = get_settings()
    return ContextOperationsModule(
        get_repository(),
        BailianModelGateway(settings),
        settings.router_model,
        input_cost_cny_per_million=settings.context_input_cost_cny_per_million,
        usage_store=ModelUsageStore(settings),
    )


@lru_cache
def get_context_retention() -> ContextRetentionService:
    settings = get_settings()
    assert settings.database_path is not None
    get_repository()
    return ContextRetentionService(settings.database_path)


@lru_cache
def get_conversation_lifecycle() -> ConversationLifecycleService:
    return ConversationLifecycleService(
        get_repository(),
        get_run_repository(),
        get_turn_executions().repository,
        get_workbench().workflow,
        get_settings().data_dir,
    )


@lru_cache
def get_turn_executions() -> TurnExecutionModule:
    settings = get_settings()
    assert settings.database_path is not None
    return TurnExecutionModule(
        TurnExecutionRepository(settings.database_path),
        get_workbench(),
    )


@lru_cache
def get_quality_operations() -> QualityOperations:
    settings = get_settings()
    assert settings.database_path is not None

    def snapshot() -> EvaluationSnapshot:
        capabilities = {
            item.capability_id: item.version
            for item in default_task_capability_registry().descriptors()
        }
        skills = {
            item.id: str(item.version)
            for item in get_skill_registry().list()
        }
        return EvaluationSnapshot(
            model_chain=[
                settings.primary_model,
                settings.primary_fallback_model,
                settings.primary_last_resort_model,
            ],
            capability_versions=capabilities,
            skill_versions=skills,
            budget_policy=QualityBudgetPolicy(
                max_p95_duration_ms=settings.quality_max_p95_duration_ms,
                max_average_cost_cny=settings.quality_max_average_cost_cny,
                max_total_cost_cny=settings.quality_max_total_cost_cny,
                min_pricing_coverage=settings.quality_min_pricing_coverage,
                enforcement=settings.quality_budget_enforcement,
            ),
        )

    return QualityOperations(
        QualityRepository(
            settings.database_path,
            database_dsn=settings.quality_database_dsn,
            allow_sqlite_fallback=settings.quality_allow_sqlite_fallback,
        ),
        lambda: WorkbenchReplayAdapter(
            get_workbench(), project_root=Path(__file__).resolve().parents[2],
        ),
        snapshot,
        worker_lease_seconds=settings.quality_worker_lease_seconds,
    )


@lru_cache
def get_feishu_connections() -> FeishuConnectionModule:
    settings = get_settings()
    assert settings.database_path is not None
    return FeishuConnectionModule(settings, FeishuConnectionRepository(settings.database_path))


@lru_cache
def get_workplace_connectors() -> WorkplaceConnectorModule:
    settings = get_settings()
    assert settings.database_path is not None
    return WorkplaceConnectorModule(
        WorkplaceConnectorRepository(settings.database_path),
        [FeishuWorkplaceConnector(settings, get_feishu_connections())],
    )


@lru_cache
def get_channel_repository() -> ChannelGatewayRepository:
    settings = get_settings()
    assert settings.database_path is not None
    return ChannelGatewayRepository(settings.database_path)


@lru_cache
def get_channel_gateway() -> ChannelGateway:
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("feishu_channel_credentials_missing")
    client = FeishuBotClient(
        FeishuBotConfig(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
    )
    transport = RoutedFeishuTransport(
        FeishuTextTransport(client),
        FeishuExternalWebhookTransport(get_channel_repository()),
    )
    return ChannelGateway(
        repository=get_channel_repository(),
        identity=get_identity(),
        workbench=get_workbench(),
        transport=transport,
        decoder=FeishuEventDecoder(),
        default_tenant_id=settings.default_tenant_id,
        default_user_id=settings.feishu_channel_default_user_id,
        lease_seconds=settings.feishu_channel_lease_seconds,
        retry_limit=settings.feishu_channel_retry_limit,
        resource_loader=FeishuResourceLoader(client),
        artifact_repository=get_repository(),
        approval_handler=get_run_approvals(),
    )


@lru_cache
def get_run_approvals() -> RunApprovalService:
    return RunApprovalService(
        repository=get_run_repository(),
        workflow=get_workbench().workflow,
        conversations=get_repository(),
        quality_operations=get_quality_operations(),
    )


def reset_container() -> None:
    if get_turn_executions.cache_info().currsize:
        get_turn_executions().close()
    if get_workbench.cache_info().currsize:
        get_workbench().close()
    if get_skill_service.cache_info().currsize:
        get_skill_service().close()
    get_workbench.cache_clear()
    get_quality_operations.cache_clear()
    get_conversation_lifecycle.cache_clear()
    get_context_operations.cache_clear()
    get_context_retention.cache_clear()
    get_turn_executions.cache_clear()
    get_feishu_connections.cache_clear()
    get_workplace_connectors.cache_clear()
    get_channel_gateway.cache_clear()
    get_channel_repository.cache_clear()
    get_run_approvals.cache_clear()
    get_skill_service.cache_clear()
    get_skill_dry_runs.cache_clear()
    get_skill_registry.cache_clear()
    get_recruitment_cases.cache_clear()
    get_run_repository.cache_clear()
    get_knowledge_index.cache_clear()
    get_policy_evaluations.cache_clear()
    get_repository.cache_clear()
    get_identity.cache_clear()
    get_settings.cache_clear()
