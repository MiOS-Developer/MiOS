import random, sys, time
words = "MiOS native RDP server H.264 EGFX AVC420 OpenH264 capture damage encode frame ack phone".split()
while True:
    sys.stdout.write(" ".join(random.choice(words) for _ in range(12)) + "\n")
    sys.stdout.flush()
    time.sleep(0.004)
