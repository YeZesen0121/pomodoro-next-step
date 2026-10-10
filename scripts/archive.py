#!/usr/bin/env python3
"""Append-only, local records for pomodoro-next-step. Requires only Python 3.10+."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

DEFAULTS = {"work_minutes": 25, "planning_ratio": 0.6}
ZONE = timezone(timedelta(hours=8))
SCHEMAS = {
    "plan": ({"plan_id", "date", "skill_version", "priority", "goal", "stage",
              "acceptance", "start_action", "key_point"},
             {"task_class", "parameters", "estimated_minutes"}),
    "feedback": ({"event_id", "plan_id", "progress"},
                 {"actual_minutes", "interruptions", "obstacle", "completed",
                  "hypotheses", "improvement_candidates", "correction_of"}),
    "revision": ({"event_id", "date", "skill_version", "confirmed", "summary",
                  "basis", "reason"}, {"old_parameters", "new_parameters"}),
}


def fail(message):
    raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def text_value(value, name, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip():
        fail(f"{name} must be a nonempty string" )


def number(value, name, nullable=False):
    if value is None and nullable:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        fail(f"{name} must be a finite nonnegative number")


def valid_date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            fail("date must be YYYY-MM-DD")
    except (ValueError, TypeError):
        fail("date must be YYYY-MM-DD")


def parameters(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        fail("parameters must contain exactly work_minutes and planning_ratio")
    if type(value["work_minutes"]) is not int or value["work_minutes"] <= 0:
        fail("work_minutes must be a positive integer")
    number(value["planning_ratio"], "planning_ratio")
    if not 0 < value["planning_ratio"] <= 1:
        fail("planning_ratio must be greater than 0 and at most 1")
    return dict(value)


def validate(data, kind, current, existing=None):
    if not isinstance(data, dict):
        fail("input must be a JSON object")
    required, optional = SCHEMAS[kind]
    if required - data.keys():
        fail("missing fields: " + ", ".join(sorted(required - data.keys())))
    if data.keys() - required - optional:
        fail("unknown fields: " + ", ".join(sorted(data.keys() - required - optional)))
    out = dict(data)
    for key in required - {"confirmed"}:
        text_value(out[key], key)
    if kind in ("plan", "revision"):
        valid_date(out["date"])
    if kind == "plan":
        if out["priority"] not in ("A", "B", "habit", "other"):
            fail("priority must be A, B, habit, or other")
        out.setdefault("task_class", None)
        text_value(out["task_class"], "task_class", True)
        fallback = existing["parameters"] if existing else current
        out["parameters"] = parameters(out.get("parameters", fallback))
        out.setdefault("estimated_minutes", None)
        number(out["estimated_minutes"], "estimated_minutes", True)
        if out["estimated_minutes"] is not None and out["estimated_minutes"] > out["parameters"]["work_minutes"]:
            fail("estimated_minutes cannot exceed work_minutes")
    elif kind == "feedback":
        for key in optional:
            out.setdefault(key, None)
        number(out["actual_minutes"], "actual_minutes", True)
        for key in ("obstacle", "correction_of"):
            text_value(out[key], key, True)
        if out["completed"] is not None and type(out["completed"]) is not bool:
            fail("completed must be a boolean or null")
        for key in ("interruptions", "hypotheses", "improvement_candidates"):
            if out[key] is not None:
                if not isinstance(out[key], list):
                    fail(f"{key} must be a list of strings or null")
                for item in out[key]:
                    text_value(item, key)
    else:
        if out["confirmed"] is not True:
            fail("revision requires confirmed=true after explicit shared agreement")
        if ("old_parameters" in out) != ("new_parameters" in out):
            fail("old_parameters and new_parameters must be supplied together")
        if "new_parameters" in out:
            out["old_parameters"] = parameters(out["old_parameters"])
            out["new_parameters"] = parameters(out["new_parameters"])
    return out


def payload(event):
    return {k: v for k, v in event.items() if k not in ("type", "recorded_at")}


def check_links(data, kind, plans, ids, current):
    if kind == "feedback":
        if data["plan_id"] not in plans:
            fail("feedback references an unknown plan_id")
        correction = data["correction_of"]
        if correction is not None:
            old = ids.get(correction)
            if not old or old["type"] != "feedback" or old["plan_id"] != data["plan_id"]:
                fail("correction_of must reference feedback for the same plan")
    if kind == "revision" and "old_parameters" in data and data["old_parameters"] != current:
        fail("old_parameters do not match current defaults")


def read_events(path):
    events, plans, ids, current = [], {}, {}, dict(DEFAULTS)
    if not path.exists():
        return events, plans, ids, current
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        line_no = raw.count(b"\n") + 1
        fail(f"{path}: line {line_no}: incomplete record; no trailing newline")
    for line_no, line in enumerate(raw.splitlines(), 1):
        try:
            event = parse_json(line.decode("utf-8"))
            if not isinstance(event, dict) or event.get("type") not in SCHEMAS:
                fail("invalid event type")
            text_value(event.get("recorded_at"), "recorded_at")
            datetime.fromisoformat(event["recorded_at"])
            kind = event["type"]
            data = validate(payload(event), kind, current)
            identity = data["plan_id"] if kind == "plan" else data["event_id"]
            registry = plans if kind == "plan" else ids
            if identity in registry:
                fail("duplicate stored identity")
            check_links(data, kind, plans, ids, current)
            if data != payload(event):
                fail("stored record is missing normalized fields")
            registry[identity] = event
            if kind == "revision" and "new_parameters" in data:
                current = dict(data["new_parameters"])
            events.append(event)
        except (ValueError, UnicodeError, TypeError) as exc:
            fail(f"{path}: line {line_no}: {exc}")
    return events, plans, ids, current


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".archive.lock"
    deadline = time.monotonic() + 10
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                fail(f"archive is locked: {lock}; verify no active writer before removing a stale lock")
            time.sleep(0.05)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock.unlink()


def append(path, event):
    encoded = (json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("ab") as stream:
        original_size = stream.tell()
        try:
            if stream.write(encoded) != len(encoded):
                fail("incomplete write")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.truncate(original_size)
            stream.flush()
            os.fsync(stream.fileno())
            raise


def brief(plan):
    return {key: plan[key] for key in ("plan_id", "priority", "goal", "stage", "skill_version",
                                      "acceptance", "estimated_minutes", "parameters")}


def latest_feedback(events):
    return {e["plan_id"]: e for e in events if e["type"] == "feedback"}


def query(args, events, plans, current):
    for value in (args.date, getattr(args, "from_date", None), getattr(args, "to_date", None)):
        if value is not None:
            valid_date(value)
    lower, upper = getattr(args, "from_date", None), getattr(args, "to_date", None)
    if args.date and (lower or upper):
        fail("--date cannot be combined with --from-date or --to-date")
    if lower and upper and lower > upper:
        fail("--from-date cannot be later than --to-date")
    selected = [p for p in plans.values() if (not args.date or p["date"] == args.date)
                and (not lower or p["date"] >= lower) and (not upper or p["date"] <= upper)]
    if args.command == "review" and args.task_class:
        selected = [p for p in selected if p["task_class"] == args.task_class]
    latest = latest_feedback(events)
    pending = [p for p in selected if p["plan_id"] not in latest]
    common = {"date": args.date, "defaults": current, "plan_count": len(selected),
              "feedback_count": sum(p["plan_id"] in latest for p in selected), "pending_count": len(pending)}
    if args.command == "context":
        ids = {p["plan_id"] for p in selected}
        feedback = [e for e in events if e["type"] == "feedback" and e["plan_id"] in ids]
        return {**common, "plans": [brief(p) for p in selected[-args.limit:]],
                "pending": [brief(p) for p in pending[-args.limit:]],
                "latest_feedback": feedback[-1] if feedback else None,
                "truncated": len(selected) > args.limit or len(pending) > args.limit}
    fields = ("actual_minutes", "interruptions", "obstacle", "completed", "hypotheses", "improvement_candidates")
    missing = {key: sum(latest.get(p["plan_id"], {}).get(key) is None for p in selected) for key in fields}
    return {**common, "from_date": lower, "to_date": upper, "task_class": args.task_class, "missing_information": missing,
            "items": [{"plan": p, "latest_feedback": latest.get(p["plan_id"]),
                       "feedback_events": sum(e["type"] == "feedback" and e["plan_id"] == p["plan_id"] for e in events)}
                      for p in selected[-args.limit:]], "truncated": len(selected) > args.limit}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(Path.home() / "Documents" / "Codex" / "pomodoro-records"))
    subs = parser.add_subparsers(dest="command", required=True)
    for kind in SCHEMAS:
        sub = subs.add_parser(kind)
        sub.add_argument("--input", required=True, help="UTF-8 JSON object file")
        sub.add_argument("--data-dir", default=argparse.SUPPRESS)
    for kind in ("context", "review"):
        sub = subs.add_parser(kind)
        sub.add_argument("--data-dir", default=argparse.SUPPRESS)
        sub.add_argument("--date", default=datetime.now(ZONE).date().isoformat() if kind == "context" else None)
        sub.add_argument("--limit", type=int, default=5 if kind == "context" else 20)
        if kind == "review":
            sub.add_argument("--task-class")
            sub.add_argument("--from-date")
            sub.add_argument("--to-date")
    args = parser.parse_args()
    if getattr(args, "limit", 1) <= 0:
        parser.error("--limit must be positive")
    try:
        directory = Path(args.data_dir).expanduser()
        path = directory / "events.jsonl"
        with locked(directory):
            events, plans, ids, current = read_events(path)
            if args.command in ("context", "review"):
                result = query(args, events, plans, current)
            else:
                source = parse_json(Path(args.input).read_text(encoding="utf-8-sig"))
                kind = args.command
                identity_key = "plan_id" if kind == "plan" else "event_id"
                registry = plans if kind == "plan" else ids
                identity = source.get(identity_key) if isinstance(source, dict) else None
                old = registry.get(identity) if isinstance(identity, str) else None
                data = validate(source, kind, current, old if kind == "plan" else None)
                if old:
                    if old["type"] != kind or payload(old) != data:
                        fail(f"{identity_key} already exists with different data")
                    result = {"status": "unchanged", "event": old}
                else:
                    check_links(data, kind, plans, ids, current)
                    event = {"type": kind, "recorded_at": datetime.now(ZONE).isoformat(), **data}
                    append(path, event)
                    verified = read_events(path)[0]
                    if not verified or verified[-1] != event:
                        fail("write verification failed")
                    result = {"status": "appended", "event": event}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"archive error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
