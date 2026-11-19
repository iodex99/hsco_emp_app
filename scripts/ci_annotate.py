"""CI helper (stdlib only): turn failed / errored tests in a JUnit XML report into GitHub annotations, so the failing
test, its message and the first lines of the traceback are visible on the run page and through the public
check-runs API — without downloading logs. Usage: python scripts/ci_annotate.py test-results/junit.xml
"""
import sys
import xml.etree.ElementTree as ET


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main(path: str) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as ex:
        print(f"::warning::no JUnit report to annotate ({ex})")
        return 0
    n = 0
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            el = case.find(kind)
            if el is None:
                continue
            n += 1
            title = f"{case.get('classname', '')}.{case.get('name', '')}"
            text = (el.get("message") or "") + "\n" + "\n".join((el.text or "").splitlines()[-25:])
            print(f"::error title={esc(title)[:200]}::{esc(text)[:3000]}")
            print(f"--- {kind}: {title}\n{text}\n")
    print(f"{n} failed / errored test(s) annotated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "test-results/junit.xml"))
