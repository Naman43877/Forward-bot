"""Reads a local broadcast.db and prints an import block to paste into the bot.

    python export_config.py                    # uses data/broadcast.db
    python export_config.py path\\to\\other.db   # or point it somewhere else

Copy the output and send it to the bot as a single message.
"""
import os
import sqlite3
import sys

LIMIT = 3900   # Telegram's per-message ceiling is 4096; leave room


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "broadcast.db")
    if not os.path.exists(path):
        print(f"No database at {path}")
        print("Run this from your project folder, or pass the path as an argument.")
        return 1

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row

    lines = ["/import"]

    for c in con.execute("SELECT * FROM channels ORDER BY title"):
        lines.append(" | ".join([
            "CHANNEL",
            str(c["chat_id"]),
            c["title"] or "",
            c["lang"] or "",
            c["affiliate_url"] or "",
            c["affiliate_text"] or "",
            c["dm_url"] or "",
            c["dm_text"] or "",
        ]))

    for g in con.execute("SELECT * FROM groups ORDER BY sort_order, name"):
        lines.append(f"GROUP | {g['name']} | {g['sort_order']}")

    for m in con.execute(
            "SELECT g.name AS gname, cg.chat_id FROM channel_groups cg "
            "JOIN groups g ON g.group_id = cg.group_id "
            "ORDER BY g.sort_order, cg.chat_id"):
        lines.append(f"MEMBER | {m['gname']} | {m['chat_id']}")

    for u in con.execute("SELECT * FROM users WHERE role='operator'"):
        lines.append(f"USER | {u['user_id']} | {u['name'] or 'operator'} | operator")

    con.close()

    if len(lines) == 1:
        print("That database is empty — nothing to export.")
        return 1

    # Split into messages that fit, each starting with /import.
    chunks, current = [], ["/import"]
    for line in lines[1:]:
        if sum(len(x) + 1 for x in current) + len(line) + 1 > LIMIT:
            chunks.append(current)
            current = ["/import"]
        current.append(line)
    chunks.append(current)

    n_ch = sum(1 for x in lines if x.startswith("CHANNEL"))
    n_gr = sum(1 for x in lines if x.startswith("GROUP"))
    n_me = sum(1 for x in lines if x.startswith("MEMBER"))
    print(f"# {n_ch} channels, {n_gr} groups, {n_me} assignments")
    print(f"# Send {'this message' if len(chunks) == 1 else f'these {len(chunks)} messages'} "
          f"to the bot.\n")

    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            print(f"----- message {i} of {len(chunks)} -----")
        print("\n".join(chunk))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
