"""Download ABS-EE / 10-D exhibits (EX-102 asset XML, EX-103, EX-99.1) for one filing.

Usage:
  python3 -I fetch_absee.py OUT_DIR CIK ACCESSION [--all]

Writes OUT_DIR/<accession>/<file>. By default fetches only *.xml (EX-102/EX-103);
--all also fetches the EX-99.1 distribution report (.htm).

User-Agent: SEC asks for "Company Name contact@domain" on www.sec.gov. In testing
(2026-10-24) www.sec.gov/Archives returned HTTP 403 "Undeclared Automated Tool" for the
project UA "cmbs-llm-research (academic project)", while data.sec.gov and efts.sec.gov
accepted it. Set SEC_USER_AGENT to a UA that includes a project contact address you
control (not a personal email) before running this against www.sec.gov.
Rate: sleeps 0.15 s between requests (<10 req/s).
"""
import json, os, ssl, sys, time, urllib.error, urllib.request

CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
UA = os.environ.get("SEC_USER_AGENT", "cmbs-llm-research (academic project)")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        return r.read()


def main(out_dir, cik, acc, fetch_all=False):
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
    try:
        idx = json.loads(get(base + "index.json"))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from www.sec.gov for {base} -- see SEC_USER_AGENT note in docstring")
    dest = os.path.join(out_dir, acc)
    os.makedirs(dest, exist_ok=True)
    for item in idx["directory"]["item"]:
        name = item["name"]
        if name.endswith(".xml") or (fetch_all and "99" in name and name.endswith(".htm")):
            time.sleep(0.15)
            data = get(base + name)
            open(os.path.join(dest, name), "wb").write(data)
            print(f"saved {name} ({len(data):,} bytes)")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    main(a[0], a[1], a[2], "--all" in sys.argv)
