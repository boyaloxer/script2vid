"""
Terminal chat with the script2vid agent.

Usage:
    python chat.py                      # auto-detects channel
    python chat.py --channel deep_thoughts
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from src.agent.agent_chat import agent_reply
from src.agent.command_queue import push_command


def main():
    parser = argparse.ArgumentParser(description="Chat with the script2vid agent")
    parser.add_argument("--channel", default="deep_thoughts", help="Channel ID")
    args = parser.parse_args()

    print(f"\n\033[36m{'='*60}\033[0m")
    print(f"\033[36m  script2vid agent — channel: {args.channel}\033[0m")
    print(f"\033[36m{'='*60}\033[0m")
    print(f"\033[90m  Type a message to chat with the agent.")
    print(f"  Start with / to queue a command (e.g. /check metrics)")
    print(f"  Type 'quit' or 'exit' to leave.\033[0m\n")

    while True:
        try:
            user_input = input("\033[97m  you > \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\033[90m  Goodbye.\033[0m")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("\033[90m  Goodbye.\033[0m")
            break

        if user_input.startswith("/"):
            cmd_text = user_input[1:].strip()
            if cmd_text:
                cmd = push_command(cmd_text, source="terminal")
                print(f"\033[33m  [queued] #{cmd['id']}: {cmd_text}\033[0m\n")
            continue

        print(f"\033[90m  thinking...\033[0m", end="", flush=True)
        try:
            reply = agent_reply(user_input, channel_id=args.channel)
            print(f"\r\033[K", end="")
            for line in reply.split("\n"):
                print(f"\033[32m  agent > {line}\033[0m")
            print()
        except Exception as e:
            print(f"\r\033[K\033[31m  error > {e}\033[0m\n")


if __name__ == "__main__":
    main()
