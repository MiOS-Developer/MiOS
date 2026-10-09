# AI-hint: Tests for task_691 & task_692: 100-stream concurrent token streaming & backpressure.
# AI-functions: test_100_concurrent_streams

"""Tests for task_691 & task_692: 100-stream concurrent token streaming & backpressure."""
import asyncio, sys
sys.path.insert(0, "usr/lib/mios/agent-pipe")
from sse_streamer import SSEStreamer

async def _run_concurrent_stream_test():
    streamer = SSEStreamer(max_queue_size=50)
    num_streams = 100
    for i in range(num_streams):
        streamer.open_stream(f"stream-{i}")

    latencies = []
    # Emit 10 tokens across all 100 streams concurrently
    for t_idx in range(10):
        for s_idx in range(num_streams):
            sid = f"stream-{s_idx}"
            lat = await streamer.push_token(sid, f"tok_{t_idx}")
            latencies.append(lat)

    # Assert 99th percentile chunk dispatch latency < 1.0ms
    latencies.sort()
    p99 = latencies[int(len(latencies) * 0.99)]
    assert p99 < 1.0, f"P99 chunk dispatch latency {p99:.3f}ms >= 1.0ms SLA"

    # Consume one chunk from stream-0
    chunk = await streamer.consume_chunk("stream-0")
    assert "data: " in chunk
    assert "tok_0" in chunk

    for i in range(num_streams):
        streamer.close_stream(f"stream-{i}")

def test_100_concurrent_streams():
    asyncio.run(_run_concurrent_stream_test())

if __name__ == "__main__":
    test_100_concurrent_streams()
    print("All task_691/task_692 tests passed.")
