from __future__ import annotations

import io
import json

import fitz
from PIL import Image

from app.domains.conversations.contracts import AttachmentRecord
from app.domains.runs import RunPayloadStore, RunPointer
from app.domains.vision import VisionEvidenceExtractor
from app.models.gateway import ModelReply


class EvidenceGateway:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ModelReply(json.dumps(self.payload, ensure_ascii=False), kwargs.get("model") or "ocr", 5, {})


class QueueEvidenceGateway:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ModelReply(json.dumps(self.payloads.pop(0), ensure_ascii=False), kwargs.get("model") or "model", 5, {})


def _attachment(path, *, extension: str, summary: dict | None = None) -> AttachmentRecord:
    return AttachmentRecord(
        id="att_1", conversation_id="conv_1", message_id="msg_1",
        display_name=path.name, mime_type="application/pdf" if extension == ".pdf" else "image/png",
        extension=extension, byte_size=path.stat().st_size, saved_path=str(path),
        extracted_text="", summary=summary or {}, created_at="2026-08-08T00:00:00Z",
    )


def test_image_evidence_is_localized_reviewed_and_offloaded(tmp_path):
    image_path = tmp_path / "notice.png"
    Image.new("RGB", (640, 320), "white").save(image_path)
    gateway = EvidenceGateway({
        "page_text": "会议日期：2026年8月18日\n预算：20?万元",
        "blocks": [
            {"text": "会议日期：2026年8月18日", "kind": "field", "bbox": [-8, 20, 1020, 80], "confidence": 0.96},
            {"text": "预算：20?万元", "kind": "field", "bbox": [100, 100, 420, 150], "confidence": 0.71},
        ],
    })
    store = RunPayloadStore(tmp_path / "payloads.db")
    pointer = RunPointer(run_id="run_1", tenant_id="tenant_1", owner_user_id="user_1")
    extractor = VisionEvidenceExtractor(
        gateway, model="qwen-vl-ocr", review_confidence=0.82, payload_store=store,
    )

    result = extractor.extract([_attachment(image_path, extension=".png")], payload_scope=pointer)

    assert result.extracted_text.startswith("[notice.png 第 1 页]")
    assert result.pages[0].blocks[0].bbox == [0, 20, 999, 80]
    assert result.pages[0].blocks[1].needs_review is True
    assert "uncertain_character" in result.pages[0].blocks[1].review_reasons
    assert result.review_block_count == 1
    assert result.payload_ref and result.payload_ref["kind"] == "vision.evidence"
    stored = store.get_json(pointer, result.payload_ref)
    assert stored["pages"][0]["blocks"][1]["text"] == "预算：20?万元"
    assert gateway.calls[0][1]["model"] == "qwen-vl-ocr"


def test_scanned_pdf_only_renders_pages_marked_as_low_text(tmp_path):
    image_bytes = io.BytesIO()
    Image.new("RGB", (300, 100), "white").save(image_bytes, format="PNG")
    pdf_path = tmp_path / "scan.pdf"
    document = fitz.open()
    for _ in range(2):
        page = document.new_page(width=300, height=100)
        page.insert_image(page.rect, stream=image_bytes.getvalue())
    document.save(pdf_path)
    document.close()
    gateway = EvidenceGateway({
        "page_text": "扫描页证据",
        "blocks": [{"text": "扫描页证据", "kind": "text", "bbox": [1, 2, 300, 50], "confidence": 0.95}],
    })
    extractor = VisionEvidenceExtractor(gateway, model="qwen-vl-ocr", max_pages=1)

    result = extractor.extract([_attachment(
        pdf_path, extension=".pdf",
        summary={"kind": "pdf", "low_text_pages": [2], "requires_vision": True},
    )])

    assert result.page_count == 1
    assert result.pages[0].page == 2
    assert result.pages[0].width == 600
    assert len(gateway.calls) == 1


def test_unlocalized_ocr_text_is_localized_by_the_vision_model(tmp_path):
    image_path = tmp_path / "notice.png"
    Image.new("RGB", (640, 320), "white").save(image_path)
    gateway = QueueEvidenceGateway([
        {"page_text": "预算上限：20万元", "blocks": []},
        {"blocks": [{
            "text": "预算上限：20万元", "kind": "field",
            "bbox": [100, 200, 700, 350], "confidence": 0.96,
        }]},
    ])
    extractor = VisionEvidenceExtractor(
        gateway, model="qwen-vl-ocr", localization_model="qwen3.7-plus",
    )

    result = extractor.extract([_attachment(image_path, extension=".png")])

    assert result.block_count == 1
    assert result.pages[0].needs_review is False
    assert [call[1]["purpose"] for call in gateway.calls] == [
        "image_evidence_extraction", "image_evidence_localization",
    ]
    assert gateway.calls[1][1]["structured"] is True


def test_model_failure_returns_reviewable_empty_page_instead_of_inventing_text(tmp_path):
    image_path = tmp_path / "unclear.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    gateway = EvidenceGateway({})
    gateway.payload = []
    extractor = VisionEvidenceExtractor(gateway, model="qwen-vl-ocr")

    result = extractor.extract([_attachment(image_path, extension=".png")])

    assert result.extracted_text == ""
    assert result.pages[0].needs_review is True
    assert result.pages[0].review_reasons == ["ocr_model_unavailable"]
