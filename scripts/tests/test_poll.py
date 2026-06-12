from pipeline.summarizer import poll_summary_batch


def test_poll_returns_ended_batch(fake_client):
    b = fake_client.messages.batches
    b.statuses["msgbatch_X"] = "ended"
    batch = poll_summary_batch(fake_client, "msgbatch_X",
                               timeout_seconds=5, poll_interval_seconds=0)
    assert batch.processing_status == "ended"
    assert b.cancelled == []  # never cancels


def test_poll_returns_pending_on_budget_exhaustion(fake_client):
    b = fake_client.messages.batches
    b.statuses["msgbatch_Y"] = "in_progress"
    batch = poll_summary_batch(fake_client, "msgbatch_Y",
                               timeout_seconds=0, poll_interval_seconds=0)
    assert batch.processing_status == "in_progress"
    assert b.cancelled == []  # no cancel — the batch is resumed next run
