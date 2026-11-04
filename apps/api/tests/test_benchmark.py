"""M10 phase 5: benchmark harness — fixed splits, blind sampling, honest metrics."""

import csv
import io
import uuid

import pytest
from sqlalchemy import select

from app.models import BenchmarkLabel
from app.services import benchmark


def test_split_is_deterministic_and_roughly_30_70():
    assert benchmark.split_for("example.com") == benchmark.split_for("example.com")
    holdout = sum(benchmark.split_for(f"d{i}.example") == "holdout" for i in range(2000))
    assert 500 < holdout < 700  # ~30 %


def test_import_ignores_unlabelled_rows_and_rejects_unknown_platform(db):
    t = uuid.uuid4().hex[:6]
    text = (f"domain,is_ecommerce,platform,notes\nwww.a{t}.example,yes,shopify,\nb{t}.example,no,,blog\n"
            f"c{t}.example,,,not labelled yet\n")  # fmt: skip
    assert benchmark.import_csv(db, text, "human_sample", "tester") == {"added": 2, "updated": 0, "skipped_unlabelled": 1}
    a = db.scalar(select(BenchmarkLabel).where(BenchmarkLabel.domain == f"a{t}.example"))
    assert a.is_ecommerce and a.platform == "shopify" and a.split in ("tune", "holdout")
    with pytest.raises(ValueError):
        benchmark.import_csv(db, f"domain,is_ecommerce,platform\nd{t}.example,yes,shopifyy\n", "human_sample", "t")
    with pytest.raises(ValueError):
        benchmark.import_csv(db, "domain,is_ecommerce\nx.example,yes\n", "my_guess", "t")


def test_sample_is_blind(db):
    out = benchmark.sample_csv(db, 10, seed=3)
    header = next(csv.reader(io.StringIO(out)))
    assert header == ["domain", "url", "found_by", "is_ecommerce", "platform", "notes"]  # no verdict column


def test_metrics_need_enough_samples_and_count_abstentions(db):
    labels = []
    for i in range(40):
        d = f"m{i}-{uuid.uuid4().hex[:6]}.example"
        labels.append(BenchmarkLabel(domain=d, is_ecommerce=i < 20, platform="shopify" if i < 20 else None,
                                     label_source="known_positive" if i < 20 else "known_negative", split="holdout"))  # fmt: skip
    db.add_all(labels)
    db.commit()
    preds = {}
    for i, lb in enumerate(labels):
        if i < 18:
            preds[lb.domain] = ("confirmed_ecommerce", "shopify")  # 18 TP
        elif i < 20:
            preds[lb.domain] = ("uncertain", None)  # 2 positives abstained
        elif i < 22:
            preds[lb.domain] = ("probable_ecommerce", "unknown")  # 2 FP
        else:
            preds[lb.domain] = ("not_ecommerce", None)  # 18 TN
    others = {lb.domain for lb in db.scalars(select(BenchmarkLabel).where(BenchmarkLabel.split == "holdout"))} - set(preds)
    preds.update({d: ("pending", None) for d in others})
    r = benchmark.evaluate(db, "holdout", preds)
    assert r["confusion"]["tp"] >= 18 and r["confusion"]["fp"] >= 2 and r["abstained"]["labelled_positive"] >= 2
    # small cells are not turned into percentages
    small = benchmark._rate(5, 10)
    assert isinstance(small, str) and small.startswith("insufficient")
    assert benchmark._rate(27, 30) == 0.9



def test_queue_is_stratified_blind_and_records_labels(db, client):
    from datetime import UTC, datetime

    from app.models import BenchmarkQueue, CrawlJob, Domain

    tag = uuid.uuid4().hex[:6]
    cases = {"not_ecommerce": {"score": {"points": 0}}, "uncertain": {"reason": "homepage has almost no server-rendered content; x"}}
    doms = []
    for i, (status, ev) in enumerate([*cases.items()] * 3):
        d = Domain(normalized_domain=f"q{i}{tag}.example", verification_status=status)
        db.add(d)
        db.flush()
        db.add(CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="verify", status="succeeded",
                        completed_at=datetime.now(UTC), result={"status": status, "evidence": ev}))  # fmt: skip
        doms.append(d.normalized_domain)
    db.commit()
    added = benchmark.build_queue(db, f"t-{tag}", seed=1, targets={"not_ecommerce": 2, "uncertain_js_shell": 2})
    assert added == {"not_ecommerce": 2, "uncertain_js_shell": 2}
    benchmark.build_queue(db, f"t-{tag}", seed=1, targets={"not_ecommerce": 2})  # re-run: never re-queues a domain
    queued = list(db.scalars(select(BenchmarkQueue.domain)))
    assert len(queued) == len(set(queued))

    nxt = client.get("/api/v1/admin/benchmark/next").json()
    assert set(nxt["item"]) == {"domain", "url"}  # blind: no verdict / stratum / source
    dom = nxt["item"]["domain"]
    assert client.post("/api/v1/admin/benchmark/label", json={"domain": dom, "verdict": "store", "platform": "shopify"}).status_code == 200
    lb = db.scalar(select(BenchmarkLabel).where(BenchmarkLabel.domain == dom))
    assert lb.is_ecommerce and lb.label_source == "human_sample" and lb.labeller
    assert client.post("/api/v1/admin/benchmark/label", json={"domain": "nope.example", "verdict": "store"}).status_code == 422
    assert client.post("/api/v1/admin/benchmark/label", json={"domain": dom, "verdict": "maybe"}).status_code == 422
    other = db.scalars(select(BenchmarkQueue).where(BenchmarkQueue.batch == f"t-{tag}", BenchmarkQueue.status == "open")).first()
    assert client.post("/api/v1/admin/benchmark/label", json={"domain": other.domain, "verdict": "inaccessible"}).status_code == 200
    db.refresh(other)
    lb2 = db.scalar(select(BenchmarkLabel).where(BenchmarkLabel.domain == other.domain))
    assert other.status == "labelled" and lb2.label == "inaccessible" and lb2.is_ecommerce is None  # not guessed
    assert lb2.labelled_at is not None and lb2.split == benchmark.split_for(other.domain)
    rep = client.get("/api/v1/admin/benchmark/report").json()
    assert "tune" in rep and isinstance(rep["holdout"], str)  # holdout hidden unless final
    before = rep["holdout_evaluations_so_far"]
    final = client.get("/api/v1/admin/benchmark/report", params={"final": True}).json()
    assert isinstance(final["holdout"], dict) and final["holdout_evaluations_so_far"] == before + 1  # every look logged
