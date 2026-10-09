#!/usr/bin/env python3
# AI-hint: Sample dynamic plugin demonstrating plugin loader discovery and execution (task_458).
import json
import sys

def main():
    if "--json" in sys.argv:
        print(json.dumps({"plugin": "hello", "status": "active", "args": sys.argv[1:]}))
    else:
        print("Hello from MiOS dynamic plugin system!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
