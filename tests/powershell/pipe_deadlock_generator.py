#!/usr/bin/env python3
# AI-hint: Pipe-buffer stress generator for ProcessStartInfo deadlock tests: concurrent, interleaved or sequential stdout/stderr.
"""
Pipe buffer stress generator for testing ProcessStartInfo deadlock conditions.
Generates configurable volumes of stdout and stderr concurrently, interleaved, or sequential.
"""
import sys
import time
import argparse

def main():
    parser = argparse.ArgumentParser(description="Pipe buffer stress generator")
    parser.add_argument("--stdout-bytes", type=int, default=0, help="Total bytes to write to stdout")
    parser.add_argument("--stderr-bytes", type=int, default=0, help="Total bytes to write to stderr")
    parser.add_argument("--mode", choices=["interleaved", "stderr-first", "stdout-first"], default="interleaved")
    parser.add_argument("--exit-code", type=int, default=0, help="Exit code to return")
    parser.add_argument("--chunk-size", type=int, default=4096, help="Chunk size for writes")
    args = parser.parse_args()

    chunk_size = args.chunk_size

    if args.mode == "stderr-first":
        written = 0
        while written < args.stderr_bytes:
            to_write = min(chunk_size, args.stderr_bytes - written)
            sys.stderr.write("E" * to_write)
            sys.stderr.flush()
            written += to_write
        written = 0
        while written < args.stdout_bytes:
            to_write = min(chunk_size, args.stdout_bytes - written)
            sys.stdout.buffer.write(b"O" * to_write)
            sys.stdout.buffer.flush()
            written += to_write
    elif args.mode == "stdout-first":
        written = 0
        while written < args.stdout_bytes:
            to_write = min(chunk_size, args.stdout_bytes - written)
            sys.stdout.buffer.write(b"O" * to_write)
            sys.stdout.buffer.flush()
            written += to_write
        written = 0
        while written < args.stderr_bytes:
            to_write = min(chunk_size, args.stderr_bytes - written)
            sys.stderr.write("E" * to_write)
            sys.stderr.flush()
            written += to_write
    else:  # interleaved
        written_out = 0
        written_err = 0
        while written_out < args.stdout_bytes or written_err < args.stderr_bytes:
            if written_err < args.stderr_bytes:
                to_write = min(chunk_size, args.stderr_bytes - written_err)
                sys.stderr.write("E" * to_write)
                sys.stderr.flush()
                written_err += to_write
            if written_out < args.stdout_bytes:
                to_write = min(chunk_size, args.stdout_bytes - written_out)
                sys.stdout.buffer.write(b"O" * to_write)
                sys.stdout.buffer.flush()
                written_out += to_write

    sys.exit(args.exit_code)

if __name__ == "__main__":
    main()
