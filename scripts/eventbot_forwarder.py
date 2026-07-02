#!/usr/bin/env python3
"""Forward new eventbot records to diytracker's ingest API.

Runs ON the eventbot box (riggi-lab), not on the diytracker server. Reads
the source-agnostic store (`events.jsonl` + `flyers/`), POSTs each not-yet-
forwarded record — content AND flyer image — to POST /api/ingest, and
tracks delivered ids in a small state file so every record is sent once.
Stdlib only; no dependencies to install.

    export INGEST_TOKEN=...   # same value as in diytracker's .env
    python3 eventbot_forwarder.py \
        --store ~/.hermes/eventbot/events \
        --url https://diytracker.ch/api/ingest

Cron (every 30 min):

    */30 * * * * INGEST_TOKEN=... /usr/bin/python3 /path/to/eventbot_forwarder.py

Delivery semantics: 201 (created) and 200 (duplicate) mark the record
forwarded; 422 (invalid) marks it forwarded too — retrying won't fix the
payload — but logs it; auth/network/5xx errors abort the run and everything
undelivered is retried next time. Records edited in the eventbot *after*
forwarding are not re-sent: final editing happens in the diytracker queue.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

STATE_FILENAME = ".diytracker-forwarded.json"


def eventbot_to_payload(rec):
    ev = rec.get("event") or {}
    artists = [a.strip() for a in (ev.get("artists") or []) if a and a.strip()]
    description = (ev.get("description") or "").strip()
    age = (ev.get("age_restriction") or "").strip()
    if age:
        description = f"{description}\n\nAge restriction: {age}".strip()
    return {
        "source": "eventbot",
        "source_id": rec.get("id"),
        "submitter": rec.get("submitter"),
        "title": ev.get("title") or ", ".join(artists) or None,
        "performers": ", ".join(artists) or None,
        "styles": ev.get("genre"),
        "description": description or None,
        "start_date": ev.get("date"),
        "doors_open": ev.get("doors_time"),
        "start_time": ev.get("start_time"),
        "venue_name": ev.get("venue"),
        "street_address": ev.get("address"),
        "city": ev.get("city"),
        "ticket_price": ev.get("price"),
        "ticket_url": ev.get("ticket_url"),
    }


def encode_multipart(payload, flyer_bytes, flyer_name):
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="event"\r\n\r\n'
        f"{json.dumps(payload)}\r\n"
    ]
    body = "".join(parts).encode("utf-8")
    if flyer_bytes is not None:
        body += (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="flyer"; filename="{flyer_name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            + flyer_bytes
            + b"\r\n"
        )
    body += f"--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def deliver(url, token, payload, flyer_bytes, flyer_name, timeout=30):
    """POST one record; returns (http_status, response_dict)."""
    body, content_type = encode_multipart(payload, flyer_bytes, flyer_name)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {}
        return e.code, detail


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default=os.path.expanduser("~/.hermes/eventbot/events"))
    ap.add_argument("--url", default="https://diytracker.ch/api/ingest")
    ap.add_argument(
        "--dry-run", action="store_true", help="list what would be sent, send nothing"
    )
    args = ap.parse_args()

    token = os.environ.get("INGEST_TOKEN")
    if not token and not args.dry_run:
        sys.exit(
            "Set INGEST_TOKEN in the environment (same value as on the diytracker server)."
        )

    jsonl = os.path.join(args.store, "events.jsonl")
    if not os.path.exists(jsonl):
        sys.exit(f"No events.jsonl at {jsonl!r}.")
    state_path = os.path.join(args.store, STATE_FILENAME)
    try:
        with open(state_path, encoding="utf-8") as f:
            forwarded = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        forwarded = {}

    sent = skipped = 0
    with open(jsonl, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    for rec in records:
        rec_id = rec.get("id")
        if not rec_id or rec_id in forwarded:
            continue
        payload = eventbot_to_payload(rec)
        flyer_bytes = flyer_name = None
        flyer_path = rec.get("flyer_image")
        if flyer_path and os.path.exists(flyer_path):
            with open(flyer_path, "rb") as fh:
                flyer_bytes = fh.read()
            flyer_name = os.path.basename(flyer_path)

        if args.dry_run:
            print(
                f"would send {rec_id}: {payload.get('title')!r}"
                f"{' + flyer' if flyer_bytes else ''}"
            )
            continue

        status, detail = deliver(args.url, token, payload, flyer_bytes, flyer_name)
        if status in (200, 201):
            forwarded[rec_id] = detail.get("status", "ok")
            sent += 1
            print(f"{rec_id}: {detail.get('status')}")
        elif status == 422:
            forwarded[rec_id] = f"invalid: {detail.get('error')}"
            skipped += 1
            print(
                f"{rec_id}: rejected as invalid ({detail.get('error')}), will not retry",
                file=sys.stderr,
            )
        else:
            # auth/network/server trouble — keep everything undelivered for
            # the next run rather than burning through the queue.
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(forwarded, fh, indent=1)
            sys.exit(
                f"{rec_id}: HTTP {status} {detail} — aborting, will retry next run."
            )

    if not args.dry_run:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(forwarded, fh, indent=1)
    print(
        f"Done: {sent} delivered, {skipped} rejected, "
        f"{len(forwarded)} total in state file."
    )


if __name__ == "__main__":
    main()
