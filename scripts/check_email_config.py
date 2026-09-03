"""
Email deliverability checker for diytracker.ch
Checks SPF, DKIM, DMARC, and reverse DNS (PTR) records.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socket

try:
    import dns.resolver
    import dns.reversename
except ImportError:
    print("dnspython not installed. Run: pip install dnspython")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
)

DOMAIN = "diytracker.ch"
DKIM_SELECTORS = ["default", "mail", "google", "k1", "s1", "s2", "dkim"]

PASS = "  ✓"
FAIL = "  ✗"
WARN = "  !"


def resolve_txt(name):
    try:
        answers = dns.resolver.resolve(name, "TXT")
        return [b"".join(r.strings).decode() for r in answers]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException):
        return []


def check_spf():
    print("\n── SPF ──────────────────────────────────────────")
    records = [r for r in resolve_txt(DOMAIN) if r.startswith("v=spf1")]

    if not records:
        print(f"{FAIL} No SPF record found for {DOMAIN}")
        print("     Add a TXT record: v=spf1 include:<your-mail-provider> -all")
        return False

    if len(records) > 1:
        print(f"{FAIL} Multiple SPF records found (only one is allowed):")
        for r in records:
            print(f"     {r}")
        return False

    record = records[0]
    print(f"{PASS} SPF record found:")
    print(f"     {record}")

    if record.endswith("-all"):
        print(f"{PASS} Policy is hard fail (-all) — good")
    elif record.endswith("~all"):
        print(f"{WARN} Policy is soft fail (~all) — consider upgrading to -all")
    elif record.endswith("?all"):
        print(f"{WARN} Policy is neutral (?all) — weak, upgrade to ~all or -all")
    elif record.endswith("+all"):
        print(f"{FAIL} Policy is pass (+all) — allows anyone to send, fix immediately")

    return True


def check_dkim():
    print("\n── DKIM ─────────────────────────────────────────")
    found = []

    for selector in DKIM_SELECTORS:
        name = f"{selector}._domainkey.{DOMAIN}"
        records = resolve_txt(name)
        found.extend(
            (selector, name, r) for r in records if "v=DKIM1" in r or "p=" in r
        )

    if not found:
        print(
            f"{FAIL} No DKIM records found (checked selectors: {', '.join(DKIM_SELECTORS)})"
        )
        print("     Enable DKIM signing in your mail provider's dashboard.")
        return False

    for selector, name, record in found:
        print(f"{PASS} DKIM record found (selector: {selector}):")
        print(f"     {name}")
        # Check key length hint from public key size — p= field
        p_start = record.find("p=")
        if p_start != -1:
            p_value = record[p_start + 2 :].split(";")[0].strip()
            key_len = len(p_value) * 6 // 8 * 8  # rough bit estimate from base64 length
            if key_len >= 256:  # 2048-bit key base64 is ~344 chars
                bits = "~2048-bit" if key_len >= 300 else "~1024-bit"
                ok = key_len >= 300
                flag = PASS if ok else WARN
                print(
                    f"{flag} Key appears to be {bits} (base64 length: {len(p_value)})"
                )
            print(f"     p={p_value[:40]}{'...' if len(p_value) > 40 else ''}")

    return True


def check_dmarc():
    print("\n── DMARC ────────────────────────────────────────")
    records = resolve_txt(f"_dmarc.{DOMAIN}")
    dmarc_records = [r for r in records if r.startswith("v=DMARC1")]

    if not dmarc_records:
        print(f"{WARN} No DMARC record found for {DOMAIN}")
        print("     Recommended: add a TXT record at _dmarc." + DOMAIN)
        print("     Example:     v=DMARC1; p=none; rua=mailto:info@" + DOMAIN)
        return False

    record = dmarc_records[0]
    print(f"{PASS} DMARC record found:")
    print(f"     {record}")

    tags = dict(part.strip().split("=", 1) for part in record.split(";") if "=" in part)

    policy = tags.get("p", "none")
    if policy == "reject":
        print(f"{PASS} Policy: reject — strongest protection")
    elif policy == "quarantine":
        print(f"{PASS} Policy: quarantine — good")
    elif policy == "none":
        print(
            f"{WARN} Policy: none — monitoring only, consider quarantine or reject later"
        )

    if "rua" in tags:
        print(f"{PASS} Aggregate reports (rua): {tags['rua']}")
    else:
        print(f"{WARN} No rua tag — you won't receive DMARC reports")

    return True


def check_ptr():
    print("\n── Reverse DNS (PTR) ────────────────────────────")
    smtp_host = os.environ.get("EMAIL_SERVER", "")

    if not smtp_host:
        print(f"{WARN} EMAIL_SERVER not set in .env — skipping PTR check")
        return

    try:
        ip = socket.gethostbyname(smtp_host)
        print(f"     {smtp_host} → {ip}")
    except socket.gaierror as e:
        print(f"{FAIL} Could not resolve {smtp_host}: {e}")
        return

    try:
        rev_name = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev_name, "PTR")
        for rdata in answers:
            ptr = str(rdata)
            print(f"{PASS} PTR record: {ip} → {ptr}")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        print(f"{FAIL} No PTR record for {ip} — your mail provider should set this")
    except dns.exception.DNSException as e:
        print(f"{FAIL} PTR lookup failed: {e}")


def check_mx():
    print("\n── MX ───────────────────────────────────────────")
    try:
        answers = dns.resolver.resolve(DOMAIN, "MX")
        for rdata in answers:
            print(f"{PASS} MX: {rdata.preference} {rdata.exchange}")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        print(f"{FAIL} No MX records found for {DOMAIN}")


if __name__ == "__main__":
    print(f"Email deliverability check for {DOMAIN}")
    print("=" * 50)

    check_spf()
    check_dkim()
    check_dmarc()
    check_ptr()
    check_mx()

    print("\n" + "=" * 50)
    print("Done. Fix any ✗ items first, then ! warnings.")
    print(f"Full report also available at: https://mxtoolbox.com/emailhealth/{DOMAIN}")
