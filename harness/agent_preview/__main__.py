"""Run the fixture conversation or inspect a single tool call from the repository root."""

import argparse
import json
import sys

from .tools import ToolError, call, contracts, load_fixture


def transcript(fixture):
    def ask(tool, **arguments):
        print(f"  {tool}({json.dumps(arguments)})")
        return call(fixture, tool, arguments)

    print("Fixture workspace · recorded examples\n")
    print("User  Show me Night Bus newest first. What would move?")
    preview = ask("plan_playlist_operation", playlist_id="NightBus", action="newest_first")
    print(f"Agent  {preview['changed_positions']} of {preview['track_count']} positions in "
          f"{preview['playlist_name']} would change. Every track entry would be kept.")
    for slot in preview["positions"]:
        suffix = " (locked)" if slot["locked"] else ""
        print(f"  {slot['position']}. {slot['before']['name']} → {slot['after']['name']}{suffix}")
    print(f"  Preview of {preview['snapshot_id']}. Nothing was queued.\n")

    print("User  Look up the earlier shuffle, run 41. Did it finish? Can I restore it?")
    for operation_id in (41, 42):
        if operation_id == 42:
            print("\nUser  And run 42 on Late Set?")
        op = ask("get_operation_status", operation_id=operation_id)["operation"]
        progress = op["progress"]
        print(f"Agent  Run {op['id']} {op['state'].replace('_', ' ')}. {op['result']['message']}")
        print(f"  The journal shows {progress['batches_done']}/{progress['total_batches']} batches acknowledged.")
        if op["recovery"]["available"]:
            print("  This run qualifies for restore review. You would compare the current playlist with the saved")
            print("  order in Shuffify before confirming. Reading this status leaves the later edit in place.")
        else:
            print("  Shuffify offers no operation-level restore for this run.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("contracts", help="print both tool contracts as JSON")
    invoke = commands.add_parser("call", help="invoke one read tool with JSON arguments")
    invoke.add_argument("name")
    invoke.add_argument("arguments")
    args = parser.parse_args()
    if args.command == "contracts":
        print(json.dumps(contracts(), indent=2))
    elif args.command == "call":
        try:
            output = call(load_fixture(), args.name, json.loads(args.arguments))
        except json.JSONDecodeError:
            print(json.dumps({"error": "invalid_arguments", "message": "Expected JSON arguments."}), file=sys.stderr)
            return 2
        except ToolError as error:
            print(json.dumps({"error": error.code, "message": str(error)}), file=sys.stderr)
            return 2
        print(json.dumps(output, indent=2))
    else:
        transcript(load_fixture())
    return 0


if __name__ == "__main__":
    sys.exit(main())
