from __future__ import annotations

import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class FeishuBotError(RuntimeError):
    """Safe, user-facing error that never includes credentials."""


@dataclass(frozen=True)
class FeishuBotConfig:
    app_id: str
    app_secret: str
    api_root: str = "https://open.feishu.cn/open-apis"
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "FeishuBotConfig":
        app_id = (
            os.getenv("UNIHR_FEISHU_BOT_APP_ID", "").strip()
            or os.getenv("UNIHR_FEISHU_APP_ID", "").strip()
        )
        app_secret = (
            os.getenv("UNIHR_FEISHU_BOT_APP_SECRET", "").strip()
            or os.getenv("UNIHR_FEISHU_APP_SECRET", "").strip()
        )
        api_root = os.getenv(
            "UNIHR_FEISHU_API_ROOT", "https://open.feishu.cn/open-apis"
        ).rstrip("/")
        if not app_id or not app_secret:
            missing = []
            if not app_id:
                missing.append("UNIHR_FEISHU_BOT_APP_ID")
            if not app_secret:
                missing.append("UNIHR_FEISHU_BOT_APP_SECRET")
            raise FeishuBotError("missing_configuration:" + ",".join(missing))
        return cls(app_id=app_id, app_secret=app_secret, api_root=api_root)


@dataclass(frozen=True)
class FeishuSendReceipt:
    chat_id: str
    message_id: str
    file_key: str | None = None
    caption_message_id: str | None = None

    def safe_dict(self) -> dict[str, str | None]:
        return {
            "status": "sent",
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "file_key": self.file_key,
            "caption_message_id": self.caption_message_id,
        }


@dataclass(frozen=True)
class FeishuImportReceipt:
    ticket: str
    document_token: str
    document_url: str
    document_type: str
    job_status: int
    shared_member_id: str | None = None
    announcement_message_id: str | None = None

    def safe_dict(self) -> dict[str, str | int | None]:
        return {
            "status": "imported",
            "ticket": self.ticket,
            "document_token": self.document_token,
            "document_url": self.document_url,
            "document_type": self.document_type,
            "job_status": self.job_status,
            "shared_member_id": self.shared_member_id,
            "announcement_message_id": self.announcement_message_id,
        }


class FeishuBotClient:
    """Minimal app-identity client for sending text and file messages to Feishu."""

    MAX_FILE_BYTES = 30 * 1024 * 1024
    MAX_IMPORT_BYTES = 20 * 1024 * 1024
    MAX_INBOUND_RESOURCE_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        config: FeishuBotConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def send_text(
        self,
        *,
        receive_id: str,
        text: str,
        receive_id_type: str = "chat_id",
    ) -> FeishuSendReceipt:
        token = self._tenant_access_token()
        message = self._send_message(
            token=token,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content={"text": text},
        )
        return FeishuSendReceipt(
            chat_id=str(message.get("chat_id") or receive_id),
            message_id=self._required_string(message, "message_id"),
        )

    def send_card(
        self, *, receive_id: str, card: dict[str, Any],
        receive_id_type: str = "chat_id",
    ) -> FeishuSendReceipt:
        token = self._tenant_access_token()
        message = self._send_message(
            token=token,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            content=card,
        )
        return FeishuSendReceipt(
            chat_id=str(message.get("chat_id") or receive_id),
            message_id=self._required_string(message, "message_id"),
        )

    def update_card(
        self, *, message_id: str, card: dict[str, Any],
    ) -> FeishuSendReceipt:
        token = self._tenant_access_token()
        payload = self._request(
            "PATCH",
            f"/im/v1/messages/{message_id}",
            token=token,
            json_body={"content": json.dumps(card, ensure_ascii=False)},
        )
        data = payload.get("data") or {}
        returned_id = str(data.get("message_id") or message_id)
        return FeishuSendReceipt(
            chat_id=str(data.get("chat_id") or ""),
            message_id=returned_id,
        )

    def download_message_resource(
        self, *, message_id: str, resource_key: str, resource_type: str,
    ) -> bytes:
        if resource_type not in {"file", "image"}:
            raise FeishuBotError("unsupported_resource_type")
        token = self._tenant_access_token()
        try:
            response = self.client.request(
                "GET",
                f"{self.config.api_root}/im/v1/messages/{message_id}/resources/{resource_key}",
                headers={"Authorization": f"Bearer {token}"},
                params={"type": resource_type},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            request_id = exc.response.headers.get("x-tt-logid", "")
            suffix = f":request_id={request_id}" if request_id else ""
            raise FeishuBotError(
                f"feishu_resource_http_{exc.response.status_code}{suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise FeishuBotError("feishu_resource_download_failed") from exc
        content = response.content
        if not content:
            raise FeishuBotError("feishu_resource_empty")
        if len(content) > self.MAX_INBOUND_RESOURCE_BYTES:
            raise FeishuBotError("feishu_resource_exceeds_20mb_limit")
        return content

    def send_file(
        self,
        *,
        receive_id: str,
        file_path: Path,
        receive_id_type: str = "chat_id",
        caption: str | None = None,
        display_name: str | None = None,
    ) -> FeishuSendReceipt:
        resolved = file_path.expanduser().resolve()
        self.validate_file(resolved)
        token = self._tenant_access_token()
        caption_message_id: str | None = None
        if caption:
            caption_message = self._send_message(
                token=token,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
                msg_type="text",
                content={"text": caption},
            )
            caption_message_id = self._required_string(caption_message, "message_id")
        file_key = self._upload_file(
            token=token, file_path=resolved, display_name=display_name,
        )
        message = self._send_message(
            token=token,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="file",
            content={"file_key": file_key},
        )
        return FeishuSendReceipt(
            chat_id=str(message.get("chat_id") or receive_id),
            message_id=self._required_string(message, "message_id"),
            file_key=file_key,
            caption_message_id=caption_message_id,
        )

    def import_docx(
        self,
        *,
        file_path: Path,
        folder_token: str,
        title: str | None = None,
        share_chat_id: str | None = None,
        share_perm: str = "view",
        announce_chat_id: str | None = None,
        poll_interval_seconds: float = 2.0,
        max_polls: int = 30,
    ) -> FeishuImportReceipt:
        """Import a local DOCX as an editable Feishu document.

        The destination folder must already be writable by the calling app. When
        ``share_chat_id`` is supplied, the app must also be a bot in that chat.
        """
        resolved = file_path.expanduser().resolve()
        self.validate_import_file(resolved)
        if not folder_token.strip():
            raise FeishuBotError("folder_token_missing")
        if share_perm not in {"view", "edit", "full_access"}:
            raise FeishuBotError("invalid_share_permission")
        if max_polls <= 0:
            raise FeishuBotError("invalid_max_polls")
        if poll_interval_seconds < 0:
            raise FeishuBotError("invalid_poll_interval")

        token = self._tenant_access_token()
        file_token = self._upload_import_source(token=token, file_path=resolved)
        ticket = self._create_import_task(
            token=token,
            file_token=file_token,
            file_extension="docx",
            document_type="docx",
            folder_token=folder_token.strip(),
            title=(title or resolved.stem).strip(),
        )
        result = self._wait_for_import(
            token=token,
            ticket=ticket,
            poll_interval_seconds=poll_interval_seconds,
            max_polls=max_polls,
        )
        document_token = self._required_string(result, "token")
        document_url = self._required_string(result, "url")
        document_type = self._required_string(result, "type")

        shared_member_id: str | None = None
        if share_chat_id:
            self._share_document_with_chat(
                token=token,
                document_token=document_token,
                chat_id=share_chat_id,
                permission=share_perm,
            )
            shared_member_id = share_chat_id

        announcement_message_id: str | None = None
        if announce_chat_id:
            message = self._send_message(
                token=token,
                receive_id=announce_chat_id,
                receive_id_type="chat_id",
                msg_type="text",
                content={
                    "text": f"《{(title or resolved.stem).strip()}》已导入飞书云文档：{document_url}"
                },
            )
            announcement_message_id = self._required_string(message, "message_id")

        return FeishuImportReceipt(
            ticket=ticket,
            document_token=document_token,
            document_url=document_url,
            document_type=document_type,
            job_status=0,
            shared_member_id=shared_member_id,
            announcement_message_id=announcement_message_id,
        )

    @classmethod
    def validate_file(cls, file_path: Path) -> None:
        if not file_path.is_file():
            raise FeishuBotError("file_not_found")
        size = file_path.stat().st_size
        if size <= 0:
            raise FeishuBotError("file_is_empty")
        if size > cls.MAX_FILE_BYTES:
            raise FeishuBotError("file_exceeds_30mb_limit")

    @classmethod
    def validate_import_file(cls, file_path: Path) -> None:
        if not file_path.is_file():
            raise FeishuBotError("file_not_found")
        if file_path.suffix.lower() != ".docx":
            raise FeishuBotError("import_requires_docx")
        size = file_path.stat().st_size
        if size <= 0:
            raise FeishuBotError("file_is_empty")
        if size > cls.MAX_IMPORT_BYTES:
            raise FeishuBotError("file_exceeds_20mb_import_limit")

    def _tenant_access_token(self) -> str:
        payload = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal/",
            json_body={
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            },
            authenticated=False,
        )
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuBotError("tenant_access_token_missing")
        return token

    def _upload_file(
        self, *, token: str, file_path: Path, display_name: str | None = None,
    ) -> str:
        upload_name = (display_name or file_path.name).strip() or file_path.name
        mime_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        with file_path.open("rb") as handle:
            payload = self._request(
                "POST",
                "/im/v1/files",
                token=token,
                form={"file_type": "stream", "file_name": upload_name},
                files={"file": (upload_name, handle, mime_type)},
            )
        data = payload.get("data") or {}
        return self._required_string(data, "file_key")

    def _upload_import_source(self, *, token: str, file_path: Path) -> str:
        mime_type = (
            mimetypes.guess_type(file_path.name)[0]
            or "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        extra = json.dumps(
            {"obj_type": "docx", "file_extension": "docx"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with file_path.open("rb") as handle:
            payload = self._request(
                "POST",
                "/drive/v1/medias/upload_all",
                token=token,
                form={
                    "file_name": file_path.name,
                    "parent_type": "ccm_import_open",
                    "size": str(file_path.stat().st_size),
                    "extra": extra,
                },
                files={"file": (file_path.name, handle, mime_type)},
            )
        data = payload.get("data") or {}
        return self._required_string(data, "file_token")

    def _create_import_task(
        self,
        *,
        token: str,
        file_token: str,
        file_extension: str,
        document_type: str,
        folder_token: str,
        title: str,
    ) -> str:
        payload = self._request(
            "POST",
            "/drive/v1/import_tasks",
            token=token,
            json_body={
                "file_extension": file_extension,
                "file_token": file_token,
                "type": document_type,
                "file_name": title,
                "point": {"mount_type": 1, "mount_key": folder_token},
            },
        )
        data = payload.get("data") or {}
        return self._required_string(data, "ticket")

    def _wait_for_import(
        self,
        *,
        token: str,
        ticket: str,
        poll_interval_seconds: float,
        max_polls: int,
    ) -> dict[str, Any]:
        for attempt in range(max_polls):
            payload = self._request(
                "GET",
                f"/drive/v1/import_tasks/{ticket}",
                token=token,
            )
            data = payload.get("data") or {}
            result = data.get("result") or {}
            if not isinstance(result, dict):
                raise FeishuBotError("invalid_import_result")
            try:
                job_status = int(result.get("job_status"))
            except (TypeError, ValueError) as exc:
                raise FeishuBotError("import_status_missing") from exc
            if job_status == 0:
                return result
            if job_status == 2:
                raise FeishuBotError("import_job_failed")
            if attempt + 1 < max_polls and poll_interval_seconds:
                time.sleep(poll_interval_seconds)
        raise FeishuBotError("import_job_timeout")

    def _share_document_with_chat(
        self,
        *,
        token: str,
        document_token: str,
        chat_id: str,
        permission: str,
    ) -> None:
        self._request(
            "POST",
            f"/drive/v1/permissions/{document_token}/members",
            token=token,
            params={"type": "docx", "need_notification": "false"},
            json_body={
                "member_type": "openchat",
                "member_id": chat_id,
                "perm": permission,
                "type": "chat",
            },
        )

    def _send_message(
        self,
        *,
        token: str,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/im/v1/messages",
            token=token,
            params={"receive_id_type": receive_id_type},
            json_body={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise FeishuBotError("invalid_message_response")
        return data

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        files: Any = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if authenticated:
            if not token:
                raise FeishuBotError("access_token_missing")
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self.client.request(
                method,
                f"{self.config.api_root}{path}",
                headers=headers,
                params=params,
                json=json_body,
                data=form,
                files=files,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            request_id = exc.response.headers.get("x-tt-logid", "")
            suffix = f":request_id={request_id}" if request_id else ""
            raise FeishuBotError(f"feishu_http_{exc.response.status_code}{suffix}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise FeishuBotError("feishu_request_failed") from exc
        if not isinstance(payload, dict):
            raise FeishuBotError("invalid_feishu_response")
        if int(payload.get("code") or 0) != 0:
            code = str(payload.get("code") or "unknown")
            request_id = response.headers.get("x-tt-logid", "")
            suffix = f":request_id={request_id}" if request_id else ""
            raise FeishuBotError(f"feishu_api_{code}{suffix}")
        return payload

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise FeishuBotError(f"response_missing_{key}")
        return value
