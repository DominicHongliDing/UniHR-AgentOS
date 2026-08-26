from app.domains.vision.evaluation import VisionEvaluationCase, VisionEvidenceEvaluator


def test_visual_eval_measures_recall_bbox_and_false_safe_rate():
    case = VisionEvaluationCase.model_validate({
        "case_id": "notice", "source_title": "notice.png", "fields": [
            {"field_id": "date", "expected_text": "日期：2026年8月18日", "page": 1,
             "bbox": [100, 100, 500, 200], "should_review": False},
            {"field_id": "count", "expected_text": "人数：?8人", "page": 1,
             "bbox": [100, 300, 400, 380], "should_review": True},
        ],
    })
    evidence = {"notice.png": [
        {"page": 1, "text": "日期：2026年8月18日", "bbox": [100, 100, 500, 200], "needs_review": False},
        {"page": 1, "text": "人数：?8人", "bbox": [110, 305, 395, 378], "needs_review": True},
    ]}

    report = VisionEvidenceEvaluator().evaluate([case], evidence)

    assert report.field_precision == 1.0
    assert report.field_recall == 1.0
    assert report.exact_match_rate == 1.0
    assert report.mean_bbox_iou > 0.8
    assert report.review_recall == 1.0
    assert report.false_safe_rate == 0.0
    assert report.cases[0]["passed"] is True
