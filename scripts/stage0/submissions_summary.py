"""Summarize filing history per CIK from data.sec.gov submissions JSON.

Usage: python3 -I submissions_summary.py OUT_DIR CIK[:label] ...
Saves raw JSON to OUT_DIR/<cik>.json and prints form counts / date range / cadence.
"""
import collections, json, os, ssl, sys, time, urllib.request

CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
UA = os.environ.get("SEC_USER_AGENT", "cmbs-llm-research (academic project)")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.load(r)


def main(out_dir, specs):
    os.makedirs(out_dir, exist_ok=True)
    for spec in specs:
        cik, _, label = spec.partition(":")
        d = get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        time.sleep(0.2)
        json.dump(d, open(os.path.join(out_dir, f"{int(cik)}.json"), "w"))
        r = d["filings"]["recent"]
        forms = collections.Counter(r["form"])
        dates = r["filingDate"]
        print(f"\n{d['name']} (CIK {int(cik)}) {label}  n={len(dates)} "
              f"range={min(dates) if dates else '-'}..{max(dates) if dates else '-'}"
              f" older_files={len(d['filings'].get('files', []))}")
        print("   forms:", dict(forms))
        # last few ABS-EE / 10-D pairs to show cadence
        rows = [(r["filingDate"][i], r["form"][i], r["accessionNumber"][i],
                 r["reportDate"][i], r["primaryDocument"][i]) for i in range(len(dates))]
        for row in [x for x in rows if x[1] in ("ABS-EE", "10-D", "10-D/A")][:6]:
            print("   ", row)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
