from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.settings import Settings

from .contracts import (
    AdapterCommitResult,
    ConnectorAuthorizationError,
    ConnectorContext,
    ConnectorProvider,
    EvidenceBundle,
    ExternalActionRequest,
    ExternalEvidence,
    ExternalReadRequest,
    ProposedAction,
    ReceiptStatus,
)
from .feishu_connection import FeishuConnectionModule


class FeishuWorkplaceConnector:
    """Real Feishu adapter. Provider URLs and tokens never enter Agent prompts or traces."""

    provider = ConnectorProvider.FEISHU
    API_ROOT = "https://open.feishu.cn/open-apis"
    READ_CAPABILITIES = {
        "calendar.read_availability",
        "directory.get_people",
        "directory.list_departments",
        "workspace.search_documents",
    }
    WRITE_CAPABILITIES = {
        "calendar.create_event",
        "messaging.send_text",
        "bitable.create_record",
        "drive.upload_artifact",
    }

    def __init__(
        self,
        settings: Settings,
        connections: FeishuConnectionModule,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.connections = connections
        self.client = client or httpx.Client(timeout=20)

    def healthy(self) -> bool:
        return not self.connections._missing_configuration()

    def supports(self, capability: str) -> bool:
        return capability in self.READ_CAPABILITIES | self.WRITE_CAPABILITIES

    def read(
        self, request: ExternalReadRequest, context: ConnectorContext
    ) -> EvidenceBundle:
        if request.capability not in self.READ_CAPABILITIES:
            raise ValueError(f"unsupported_read_capability:{request.capability}")
        token = self._token(context.tenant_id, context.user_id)
        arguments = request.arguments
        if request.capability == "calendar.read_availability":
            data = self._request(
                "POST",
                "/calendar/v4/freebusy/list",
                token,
                json_body={
                    "time_min": self._required(arguments, "time_min"),
                    "time_max": self._required(arguments, "time_max"),
                    "user_id": self._required(arguments, "user_id"),
                },
            )
            items = data.get("freebusy_list") or data.get("items") or []
            return self._bundle(request.capability, items, title_key=None)

        if request.capability == "directory.get_people":
            user_ids = arguments.get("user_ids") or []
            if not isinstance(user_ids, list) or not user_ids or len(user_ids) > 50:
                raise ValueError("user_ids_must_contain_1_to_50_items")
            params: list[tuple[str, str]] = [
                ("user_ids", str(user_id)) for user_id in user_ids
            ]
            params.extend(
                [
                    ("user_id_type", str(arguments.get("user_id_type") or "open_id")),
                    (
                        "department_id_type",
                        str(arguments.get("department_id_type") or "open_department_id"),
                    ),
                ]
            )
            data = self._request(
                "GET", "/contact/v3/users/batch", token, params=params
            )
            return self._bundle(
                request.capability,
                data.get("items") or data.get("users") or [],
                title_key="name",
            )

        if request.capability == "directory.list_departments":
            department_id = quote(
                str(arguments.get("department_id") or "0"), safe=""
            )
            data = self._request(
                "GET",
                f"/contact/v3/departments/{department_id}/children",
                token,
                params={
                    "department_id_type": str(
                        arguments.get("department_id_type") or "open_department_id"
                    ),
                    "fetch_child": bool(arguments.get("fetch_child", False)),
                    "page_size": min(50, max(1, int(arguments.get("page_size") or 50))),
                    **(
                        {"page_token": str(arguments["page_token"])}
                        if arguments.get("page_token")
                        else {}
                    ),
                },
            )
            bundle = self._bundle(
                request.capability, data.get("items") or [], title_key="name"
            )
            if data.get("has_more"):
                bundle.warnings.append("more_results_available")
            return bundle

        search_key = str(arguments.get("search_key") or "").strip()
        if not search_key:
            raise ValueError("search_key_required")
        data = self._request(
            "POST",
            "/suite/docs-api/search/object",
            token,
            json_body={
                "search_key": search_key,
                "count": min(50, max(1, int(arguments.get("count") or 20))),
                "offset": max(0, int(arguments.get("offset") or 0)),
                **(
                    {"docs_types": arguments["docs_types"]}
                    if arguments.get("docs_types")
                    else {}
                ),
            },
        )
        items = data.get("docs_entities") or data.get("items") or []
        return self._bundle(request.capability, items, title_key="title")

    def normalize_action(
        self, request: ExternalActionRequest, context: ConnectorContext
    ) -> dict[str, Any]:
        if request.capability not in self.WRITE_CAPABILITIES:
            raise ValueError(f"unsupported_write_capability:{request.capability}")
        raw = request.arguments
        if request.capability == "calendar.create_event":
            normalized = {
                "calendar_id": self._required(raw, "calendar_id"),
                "summary": self._required(raw, "summary"),
                "start_time": self._event_time(raw.get("start_time") or raw.get("starts_at")),
                "end_time": self._event_time(raw.get("end_time") or raw.get("ends_at")),
            }
            for key in ("description", "location", "attendee_ability"):
                if raw.get(key) is not None:
                    normalized[key] = raw[key]
            return normalized
        if request.capability == "messaging.send_text":
            return {
                "receive_id_type": str(raw.get("receive_id_type") or "open_id"),
                "receive_id": self._required(raw, "receive_id"),
                "text": self._required(raw, "text"),
            }
        if request.capability == "bitable.create_record":
            fields = raw.get("fields")
            if not isinstance(fields, dict) or not fields:
                raise ValueError("fields_required")
            return {
                "app_token": self._required(raw, "app_token"),
                "table_id": self._required(raw, "table_id"),
                "fields": fields,
            }

        artifact_path = Path(self._required(raw, "artifact_path")).resolve()
        if not artifact_path.is_file():
            raise ValueError("artifact_file_not_found")
        size = artifact_path.stat().st_size
        if size <= 0 or size > 20 * 1024 * 1024:
            raise ValueError("artifact_file_size_not_supported")
        return {
            "artifact_path": str(artifact_path),
            "file_name": str(raw.get("file_name") or artifact_path.name),
            "parent_type": str(raw.get("parent_type") or "explorer"),
            "parent_node": self._required(raw, "parent_node"),
            "file_size": size,
            "file_sha256": self._sha256(artifact_path),
        }

    def commit(
        self, proposal: ProposedAction, *, idempotency_key: str
    ) -> AdapterCommitResult:
        try:
            token = self._token(proposal.tenant_id, proposal.owner_user_id)
            args = proposal.normalized_arguments
            capability = proposal.capability
            if capability == "calendar.create_event":
                calendar_id = quote(str(args["calendar_id"]), safe="")
                body = {key: value for key, value in args.items() if key != "calendar_id"}
                data = self._request(
                    "POST",
                    f"/calendar/v4/calendars/{calendar_id}/events",
                    token,
                    params={"idempotency_key": idempotency_key},
                    json_body=body,
                )
                event = data.get("event") or data
                return self._success(event.get("event_id") or event.get("id"), data)
            if capability == "messaging.send_text":
                data = self._request(
                    "POST",
                    "/im/v1/messages",
                    token,
                    params={"receive_id_type": args["receive_id_type"]},
                    json_body={
                        "receive_id": args["receive_id"],
                        "msg_type": "text",
                        "content": json.dumps({"text": args["text"]}, ensure_ascii=False),
                    },
                )
                message = data.get("message") or data
                return self._success(message.get("message_id") or message.get("id"), data)
            if capability == "bitable.create_record":
                app_token = quote(str(args["app_token"]), safe="")
                table_id = quote(str(args["table_id"]), safe="")
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    token,
                    params={"client_token": idempotency_key[:64]},
                    json_body={"fields": args["fields"]},
                )
                record = data.get("record") or data
                return self._success(record.get("record_id") or record.get("id"), data)
            if capability == "drive.upload_artifact":
                path = Path(str(args["artifact_path"]))
                if not path.is_file() or self._sha256(path) != args["file_sha256"]:
                    return AdapterCommitResult(
                        status=ReceiptStatus.FAILED,
                        error_code="artifact_changed_after_approval",
                    )
                with path.open("rb") as handle:
                    data = self._request(
                        "POST",
                        "/drive/v1/files/upload_all",
                        token,
                        form={
                            "file_name": args["file_name"],
                            "parent_type": args["parent_type"],
                            "parent_node": args["parent_node"],
                            "size": str(args["file_size"]),
                        },
                        files={
                            "file": (
                                args["file_name"],
                                handle,
                                mimetypes.guess_type(args["file_name"])[0]
                                or "application/octet-stream",
                            )
                        },
                    )
                file_data = data.get("file") or data
                return self._success(file_data.get("file_token") or file_data.get("token"), data)
            return AdapterCommitResult(
                status=ReceiptStatus.FAILED, error_code="unsupported_write_capability"
            )
        except ConnectorAuthorizationError as exc:
            return AdapterCommitResult(status=ReceiptStatus.FAILED, error_code=str(exc))
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return AdapterCommitResult(
                status=ReceiptStatus.FAILED,
                error_code=self._safe_error(exc),
            )

    def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        params: Any = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        files: Any = None,
    ) -> dict[str, Any]:
        response = self.client.request(
            method,
            f"{self.API_ROOT}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json=json_body,
            data=form,
            files=files,
        )
        response.raise_for_status()
        payload = response.json()
        code = int(payload.get("code") or 0)
        if code != 0:
            raise RuntimeError(
                f"feishu_api_error:{code}:{str(payload.get('msg') or 'unknown')[:100]}"
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _token(self, tenant_id: str, owner_user_id: str) -> str:
        try:
            return self.connections.access_token_for(
                tenant_id=tenant_id, owner_user_id=owner_user_id
            )
        except RuntimeError as exc:
            raise ConnectorAuthorizationError(str(exc)) from exc

    def _bundle(
        self, capability: str, items: Any, *, title_key: str | None
    ) -> EvidenceBundle:
        if not isinstance(items, list):
            items = [items]
        evidence: list[ExternalEvidence] = []
        for index, item in enumerate(items[:50], start=1):
            safe_item = item if isinstance(item, dict) else {"value": item}
            source_id = str(
                safe_item.get("token")
                or safe_item.get("open_id")
                or safe_item.get("department_id")
                or safe_item.get("id")
                or f"feishu-{index}"
            )
            title = (
                str(safe_item.get(title_key) or source_id)
                if title_key
                else f"飞书忙闲记录 {index}"
            )
            evidence.append(
                ExternalEvidence(
                    source_id=source_id,
                    title=title,
                    content=json.dumps(safe_item, ensure_ascii=False, sort_keys=True),
                    url=str(safe_item.get("url") or "") or None,
                    metadata={"provider": "feishu", "live": True},
                )
            )
        return EvidenceBundle(
            provider=self.provider, capability=capability, evidence=evidence
        )

    @staticmethod
    def _required(arguments: dict[str, Any], key: str) -> str:
        value = str(arguments.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key}_required")
        return value

    @staticmethod
    def _event_time(value: Any) -> dict[str, str]:
        if isinstance(value, dict) and value.get("timestamp"):
            return {
                "timestamp": str(value["timestamp"]),
                "timezone": str(value.get("timezone") or "Asia/Shanghai"),
            }
        if not value:
            raise ValueError("event_time_required")
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("event_time_timezone_required")
        return {"timestamp": str(int(parsed.timestamp())), "timezone": "Asia/Shanghai"}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _success(external_id: Any, data: dict[str, Any]) -> AdapterCommitResult:
        return AdapterCommitResult(
            status=ReceiptStatus.SUCCEEDED,
            external_ids=[str(external_id)] if external_id else [],
            details={"provider": "feishu", "response_keys": sorted(data.keys())},
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        value = str(exc).replace("\n", " ")[:160]
        return value or exc.__class__.__name__
