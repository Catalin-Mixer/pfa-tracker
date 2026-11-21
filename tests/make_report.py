"""Runs the whole test suite and writes test-report.html next to the README.

Usage:  cd ~/pfa-tracker && python3 tests/make_report.py
"""
import ast
import html
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test-report.html"


def module_doc(path: Path) -> str:
    try:
        return (ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or "").split("\n\n")[0].replace("\n", " ")
    except (OSError, SyntaxError):
        return ""


def readable(name: str) -> tuple[str, str]:
    base, _, params = name.partition("[")
    text = base.removeprefix("test_").replace("_", " ")
    return text[:1].upper() + text[1:], params.rstrip("]")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        started = time.time()
        proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                               f"--junitxml={xml_path}", "-o", "junit_family=xunit1"],
                              cwd=ROOT, capture_output=True, text=True)
        elapsed = time.time() - started
        suite = ET.parse(xml_path).getroot()
    cases = suite.iter("testcase")
    files: "OrderedDict[str, list[dict]]" = OrderedDict()
    for c in cases:
        module = c.get("classname", "").split(".")[-1]
        result, detail = "passed", ""
        for tag, label in (("failure", "FAILED"), ("error", "ERROR")):
            el = c.find(tag)
            if el is not None:
                result, detail = label, (el.get("message") or "")[:400]
        skipped = c.find("skipped")
        if skipped is not None:
            msg = skipped.get("message") or ""
            if skipped.get("type") == "pytest.xfail" or msg.startswith("xfail") or "xfail" in (skipped.get("type") or ""):
                result, detail = "expected failure", msg.removeprefix("xfail").strip(" :")
            else:
                result, detail = "skipped", msg
        title, params = readable(c.get("name", ""))
        files.setdefault(module, []).append({"title": title, "params": params, "result": result,
                                             "detail": detail, "time": float(c.get("time") or 0)})
    total = sum(len(v) for v in files.values())
    count = lambda r: sum(1 for v in files.values() for t in v if t["result"] == r)
    passed, xfail, failed = count("passed"), count("expected failure"), count("FAILED") + count("ERROR")
    ok = failed == 0 and proc.returncode == 0
    pytest_version = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True, text=True).stdout.strip()

    e = html.escape
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PFA tax tracker – test report</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;color:#1f2933;max-width:1100px;margin:24px auto;padding:0 20px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 6px}} .muted{{color:#66727f}}
.verdict{{padding:12px 16px;border-radius:8px;margin:14px 0;font-size:16px}}
.ok{{background:#e8f5ec;border:1px solid #b7dfc2}} .bad{{background:#fdecea;border:1px solid #f5c2bd}}
table{{border-collapse:collapse;width:100%;margin:6px 0 10px}} th,td{{border-bottom:1px solid #e2e6eb;padding:5px 8px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa;font-size:12px;color:#66727f}} td.n{{text-align:right;white-space:nowrap}}
.r-passed{{color:#1a7f37}} .r-expected{{color:#9a6700}} .r-FAILED,.r-ERROR{{color:#b42318;font-weight:600}}
code{{background:#eef1f4;padding:1px 4px;border-radius:4px;font-size:12px}} .p{{color:#66727f;font-size:12px;word-break:break-all;max-width:420px}}
</style></head><body>
<h1>PFA tax tracker – test report</h1>
<div class="muted">{datetime.now():%Y-%m-%d %H:%M} · Python {platform.python_version()} · {e(pytest_version)} · {elapsed:.1f} s · tests run on scratch copies, never on your data</div>
<div class="verdict {'ok' if ok else 'bad'}"><b>{'All tests behave as intended.' if ok else 'Some tests FAILED — see below.'}</b>
{total} tests: <b>{passed} passed</b>, <b>{xfail} expected failures</b> (the intended limits, failing on purpose), <b>{failed} failed</b>.</div>
<h2>Summary by area</h2><table><tr><th>Test file</th><th>What it checks</th><th class="n">Tests</th><th class="n">Passed</th><th class="n">Expected failures</th><th class="n">Failed</th></tr>"""]
    for module, tests in files.items():
        doc = module_doc(ROOT / "tests" / f"{module}.py")
        c = lambda r: sum(1 for t in tests if t["result"] == r)
        out.append(f"<tr><td><a href='#{e(module)}'><code>{e(module)}.py</code></a></td><td>{e(doc)}</td><td class='n'>{len(tests)}</td>"
                   f"<td class='n'>{c('passed')}</td><td class='n'>{c('expected failure')}</td><td class='n'>{c('FAILED') + c('ERROR')}</td></tr>")
    out.append("</table>")
    limits = [t for v in files.values() for t in v if t["result"] == "expected failure"]
    if limits:
        out.append("<h2>Intended limits (tests that fail on purpose)</h2><p class='muted'>Each of these describes something the tracker deliberately does not do. They are expected to fail; if one ever passed, the run would be marked as failed.</p><table><tr><th>Test</th><th>Why it fails on purpose</th></tr>")
        out += [f"<tr><td>{e(t['title'])}</td><td>{e(t['detail'])}</td></tr>" for t in limits]
        out.append("</table>")
    out.append("<h2>Every test</h2>")
    for module, tests in files.items():
        out.append(f"<h3 id='{e(module)}'><code>{e(module)}.py</code> <span class='muted'>— {e(module_doc(ROOT / 'tests' / f'{module}.py'))}</span></h3>")
        out.append("<table><tr><th>Test</th><th>Case</th><th>Result</th><th class='n'>Time</th></tr>")
        for t in tests:
            cls = "r-" + t["result"].split()[0]
            detail = f"<div class='p'>{e(t['detail'])}</div>" if t["detail"] and t["result"] != "expected failure" else ""
            out.append(f"<tr><td>{e(t['title'])}{detail}</td><td class='p'>{e(t['params'])}</td><td class='{cls}'>{e(t['result'])}</td><td class='n'>{t['time']:.2f} s</td></tr>")
        out.append("</table>")
    if not ok:
        out.append(f"<h2>pytest output</h2><pre>{e(proc.stdout[-20000:])}</pre>")
    out.append("</body></html>")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"{total} tests: {passed} passed, {xfail} expected failures, {failed} failed -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
