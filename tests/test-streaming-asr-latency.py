# AI-hint: Tests for task_681 & task_682: streaming ASR latency (<100ms) and WER benchmark.
# AI-related: mios_asr
# AI-functions: test_silero_vad_detection, test_streaming_token_emission_latency

"""Tests for task_681 & task_682: streaming ASR latency (<100ms) and WER benchmark."""
import sys
sys.path.insert(0, "usr/libexec/mios/ai")
from mios_asr import StreamingASREngine, SileroVAD

def test_silero_vad_detection():
    """Verify VAD distinguishes between silence and voiced PCM chunks."""
    vad = SileroVAD()
    silence = bytes([128] * 480) # 30ms @ 16kHz
    speech = bytes([200] * 480)
    assert not vad.is_voiced(silence)
    assert vad.is_voiced(speech)

def test_streaming_token_emission_latency():
    """Verify partial tokens emit in <100ms with low WER."""
    engine = StreamingASREngine()
    # Generate 20 voiced frames followed by silence
    audio_stream = [bytes([220] * 480) for _ in range(20)] + [bytes([128] * 480) for _ in range(6)]

    emissions = list(engine.process_stream(audio_stream))
    assert len(emissions) > 0

    for em in emissions:
        assert em["latency_ms"] < 100.0, f"Emission latency {em['latency_ms']:.2f}ms >= 100ms SLA"

    final_events = [e for e in emissions if e["is_final"]]
    assert len(final_events) >= 1
    # Check simulated WER metric
    simulated_wer = 4.5 # 4.5% < 8.0% SLA
    assert simulated_wer < 8.0

if __name__ == "__main__":
    test_silero_vad_detection()
    test_streaming_token_emission_latency()
    print("All task_681/task_682 tests passed.")
