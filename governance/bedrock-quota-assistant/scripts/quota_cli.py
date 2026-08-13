#!/usr/bin/env python3
"""
Bedrock Quota Agent CLI - Interactive client for AgentCore.

A terminal-based alternative to the Slack bot for interacting with the
Bedrock Quota Agent. Supports multi-turn conversations with session persistence.

Required environment variables:
  AGENTCORE_ARN - ARN of your AgentCore runtime

Optional environment variables:
  AWS_REGION - AWS region (overrides ARN-derived region)
  AWS_DEFAULT_REGION - AWS region fallback

Usage:
  source scripts/setup-local.sh
  python scripts/quota_cli.py [options]

Options:
  --session NAME    Use a named session (default: generates random ID)
  --actor NAME      Set actor ID (default: cli-user)
  --region REGION   AWS region (default: from env or extracted from AGENTCORE_ARN)
  --no-color        Disable colored output
"""

import argparse
import boto3
import json
import os
import readline  # Enables arrow key history in input()
import sys
import threading
import time
import uuid
from datetime import datetime


# ANSI color codes
class Colors:
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    DIM = '\033[2m'
    RESET = '\033[0m'


# Global flag for color support
USE_COLOR = True


def color(text: str, color_code: str) -> str:
    """Apply color if enabled."""
    if USE_COLOR:
        return f"{color_code}{text}{Colors.RESET}"
    return text


def print_system(msg: str):
    """Print system message."""
    print(color(msg, Colors.YELLOW))


def print_error(msg: str):
    """Print error message."""
    print(color(f"Error: {msg}", Colors.RED))


def print_agent(msg: str):
    """Print agent response."""
    print(color("\nAgent:", Colors.GREEN))
    print(msg)


class Spinner:
    """Simple spinner for long-running operations."""
    
    def __init__(self, message: str = "Thinking"):
        self.message = message
        self.running = False
        self.thread = None
    
    def _spin(self):
        chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while self.running:
            char = chars[i % len(chars)]
            status = color(f"\r{char} {self.message}...", Colors.DIM)
            sys.stdout.write(status)
            sys.stdout.flush()
            time.sleep(0.1)  # nosemgrep: arbitrary-sleep — intentional spinner animation delay
            i += 1
        # Clear the spinner line
        sys.stdout.write('\r' + ' ' * (len(self.message) + 10) + '\r')
        sys.stdout.flush()
    
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin)
        self.thread.start()
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()


def get_required_env(name: str, example: str) -> str:
    """Get required environment variable or exit with helpful message."""
    value = os.environ.get(name)
    if not value:
        print_error(f"{name} environment variable is required")
        print(f"  Set it to your {name}, e.g.:")
        print(f"    export {name}={example}")
        print(f"\n  Or source your setup file:")
        print(f"    source scripts/setup-local.sh")
        sys.exit(1)
    return value


def invoke_agent(client, runtime_arn: str, prompt: str, session_id: str, actor_id: str) -> str:
    """Invoke the AgentCore runtime."""
    payload = {
        "prompt": prompt,
        "session_id": session_id,
        "actor_id": actor_id
    }
    
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps(payload).encode()
    )
    result = json.loads(response['response'].read().decode())
    return result.get("result", "No response from agent")


def print_help():
    """Print available commands."""
    print("""
Commands:
  /new [name]     Start a new session (optionally named)
  /actor [name]   Change actor ID (for multi-user testing)
  /session        Show current session info
  /help           Show this help
  /quit           Exit the CLI

Examples:
  "Check quotas for claude haiku"
  "What about us-east-1?"
  "Refresh the quotas for nova pro"
""")


def _region_from_arn(arn: str) -> str:
    """Extract region from an AgentCore ARN like arn:aws:bedrock-agentcore:eu-west-1:..."""
    parts = arn.split(":")
    if len(parts) >= 4:
        return parts[3]
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Bedrock Quota Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--session", "-s", help="Named session ID")
    parser.add_argument("--actor", "-a", default="cli-user", help="Actor ID (default: cli-user)")
    parser.add_argument("--region", "-r", help="AWS region")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = parser.parse_args()
    
    global USE_COLOR
    USE_COLOR = not args.no_color and sys.stdout.isatty()
    
    # Get configuration
    runtime_arn = get_required_env(
        "AGENTCORE_ARN",
        "arn:aws:bedrock-agentcore:eu-west-1:YOUR_ACCOUNT_ID:runtime/YOUR_RUNTIME_ID"
    )
    # Region priority: --region flag > extracted from ARN > AWS_REGION env > AWS_DEFAULT_REGION env
    # The ARN is the source of truth for where the runtime lives.
    region = (
        args.region
        or _region_from_arn(runtime_arn)
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    
    # Initialize session
    if args.session:
        session_id = f"cli-{args.session}"
    else:
        session_id = f"cli-{uuid.uuid4().hex[:8]}"
    actor_id = args.actor
    
    # Create client
    client = boto3.client('bedrock-agentcore', region_name=region)
    
    # Print banner
    print(color("=" * 60, Colors.CYAN))
    print(color("  Bedrock Quota Agent CLI", Colors.CYAN))
    print(color("=" * 60, Colors.CYAN))
    print_system(f"Session: {session_id}")
    print_system(f"Actor: {actor_id}")
    print_system(f"Region: {region}")
    print(color("Type /help for commands, /quit to exit", Colors.DIM))
    print()
    
    # Main loop
    while True:
        try:
            prompt = input(color("You: ", Colors.CYAN)).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break
        
        if not prompt:
            continue
        
        # Handle commands
        if prompt.startswith("/"):
            cmd_parts = prompt[1:].split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            cmd_arg = cmd_parts[1] if len(cmd_parts) > 1 else None
            
            if cmd in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            elif cmd == "help":
                print_help()
            elif cmd == "new":
                if cmd_arg:
                    session_id = f"cli-{cmd_arg}"
                else:
                    session_id = f"cli-{uuid.uuid4().hex[:8]}"
                print_system(f"New session: {session_id}")
            elif cmd == "actor":
                if cmd_arg:
                    actor_id = cmd_arg
                    print_system(f"Actor changed to: {actor_id}")
                else:
                    print_system(f"Current actor: {actor_id}")
                    print("Usage: /actor <name>")
            elif cmd == "session":
                print_system(f"Session: {session_id}")
                print_system(f"Actor: {actor_id}")
                print_system(f"Region: {region}")
            else:
                print_error(f"Unknown command: /{cmd}")
                print("Type /help for available commands")
            continue
        
        # Invoke agent
        spinner = Spinner("Thinking")
        try:
            spinner.start()
            response = invoke_agent(client, runtime_arn, prompt, session_id, actor_id)
            spinner.stop()
            print_agent(response)
        except KeyboardInterrupt:
            spinner.stop()
            print_system("\nRequest cancelled")
        except Exception as e:
            spinner.stop()
            print_error(str(e))


if __name__ == "__main__":
    main()
