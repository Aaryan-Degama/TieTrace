"""Tests for obsidian_export.py (the Obsidian live export of a newsroom memory graph).

Run:  FALKORDB_PORT=6379 uv run python tests/test_obsidian.py [name-substring ...]
Builder's tests. The scratch graph newsroom:zz-test-obs is filled with the real server tools on the real
research:obesity-coke graph (read-only), exported to a temp vault, and deleted at the end. No API calls.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TIETRACE_NEWSROOM"] = "zz-test-obs"  # never the real alpha newsroom
import memory  # noqa: E402
import obsidian_export as ox  # noqa: E402
import research  # noqa: E402
import server  # noqa: E402

DB = research._conn()
NR = "newsroom:zz-test-obs"
BLAIR, HILL = "A5105003630", "A5031318756"  # documented GEBN case (public_ids)
NONE_EXPERT = "A5067322664"                 # tier NONE in research:obesity-coke (see test_memory)
VAULT = Path(tempfile.mkdtemp(prefix="tt-vault-"))
FOLDER = VAULT / "TieTrace" / "zz-test-obs"
S = {}  # ids and names made by setup()


def ok(out):
    assert out["ok"] is True, out.get("error")
    return out["data"]


def setup():
    (VAULT / ".obsidian").mkdir()
    S["names"] = {r["expert_id"]: r["label"] for r in ok(server.check_case([HILL, BLAIR], True))["results"]}
    assert not any(n.startswith("Expert #") for n in S["names"].values()), "fixture: public names expected"
    c = ok(server.open_case("Private story", "Coca-Cola", "obesity", [HILL, BLAIR, NONE_EXPERT]))
    S["private"] = c["case_id"]
    S["checks"] = {r["expert_id"]: r["check_id"] for r in ok(server.check_case(case_id=c["case_id"]))["results"]}
    ok(server.tie_report(NONE_EXPERT, case_id=c["case_id"]))  # tier NONE with its no-tie scope
    S["recheck"] = ok(server.recheck(S["checks"][BLAIR]))["check_id"]
    p = ok(server.open_case("Public story", "Coca-Cola", "obesity", [BLAIR], True))
    S["public"] = p["case_id"]
    ok(server.tie_report(BLAIR, case_id=p["case_id"]))


def notes():
    return {p.name: p.read_text(encoding="utf-8") for p in FOLDER.glob("*.md")}


def test_first_export_writes_notes_and_never_writes_the_graph():
    before = DB.select_graph(NR).query("MATCH (n) RETURN count(n)").result_set[0][0]
    w, r = ox.export(VAULT, "zz-test-obs")
    assert w > 10 and r == 0, (w, r)
    after = DB.select_graph(NR).query("MATCH (n) RETURN count(n)").result_set[0][0]
    assert before == after
    src = (ROOT / "obsidian_export.py").read_text(encoding="utf-8")
    assert ".query(" not in src and "research:" not in src.replace("research:{c['research']}", "")
    assert "openalex.org" not in src and "requests" not in src


def test_notes_links_tags_and_no_tie_scope():
    n = notes()
    case_files = [k for k in n if k.startswith("Case - Private story")]
    assert len(case_files) == 1 and S["private"][-6:] in case_files[0]
    case = n[case_files[0]]
    for eid in (HILL, BLAIR, NONE_EXPERT):
        assert f"[[{eid}|Expert #" in case and f"{eid}.md" in n
    blair = n[f"{BLAIR}.md"]
    assert "tier/A1" in blair and "[[Paper 10." in blair and "[[Funder - Coca-Cola]]" in blair
    assert "|Tier-A co-author]]" in blair and "https://doi.org/10." in blair
    assert "Check history" in blair and S["checks"][BLAIR] in blair and S["recheck"] in blair
    none = n[f"{NONE_EXPERT}.md"]
    assert "tier/NONE" in none and "query `q3_bfs_v1`" in none and "hops" in none and "coverage" in none
    for link in set(re.findall(r"\[\[([^|\]]+)", "".join(n.values()))):
        assert f"{link}.md" in n, f"dangling link {link}"
    paper = next(v for k, v in n.items() if k.startswith("Paper "))
    assert "https://doi.org/" in paper and ("on-topic" in paper or "off-topic" in paper) and "[[Funder - " in paper
    assert "TieTrace index.md" in n and "TieTrace README.md" in n and "tag:#tier/A1" in n["TieTrace README.md"]
    for k, v in n.items():
        if k != "TieTrace README.md":
            assert v.startswith(ox.MARKER), k


def test_names_only_for_the_public_case_and_no_banned_words():
    n = notes()
    hill_name, blair_name = S["names"][HILL], S["names"][BLAIR]
    assert not any(hill_name in v for v in n.values()), "a non-public expert was named"
    named = sorted(k for k, v in n.items() if blair_name in v)
    assert named == sorted([f"{BLAIR}.md", next(k for k in n if k.startswith("Case - Public story"))]), named
    private_case = n[next(k for k in n if k.startswith("Case - Private story"))]
    assert blair_name not in private_case and f"[[{BLAIR}|Expert #2]]" in private_case
    for v in n.values():
        assert not re.search(r"\b(independent|conflicted|independence|independently)\b", v, re.I)


def test_second_export_writes_nothing():
    assert ox.export(VAULT, "zz-test-obs") == (0, 0)


def test_stale_owned_notes_removed_user_notes_kept():
    (FOLDER / "Old paper.md").write_text(ox.note({"type": "paper"}, "# gone"), encoding="utf-8")
    (FOLDER / "My own note.md").write_text("# mine\n[[A5105003630]]\n", encoding="utf-8")
    (VAULT / "Elsewhere.md").write_text(ox.note({}, "outside the folder"), encoding="utf-8")
    assert ox.export(VAULT, "zz-test-obs") == (0, 1)
    assert not (FOLDER / "Old paper.md").exists()
    assert (FOLDER / "My own note.md").exists() and (VAULT / "Elsewhere.md").exists()


def test_new_memory_shows_up_and_index_counts():
    ok(server.open_case("Later story", "Coca-Cola", "obesity", [HILL]))
    w, r = ox.export(VAULT, "zz-test-obs")
    assert w >= 3 and r == 0, (w, r)  # the new case, Hill's note (new section) and the index
    n = notes()
    assert any(k.startswith("Case - Later story") for k in n) and "Not checked yet" in n[f"{HILL}.md"]
    assert "- Case: 3" in n["TieTrace index.md"]


def test_refuses_a_folder_that_is_not_a_vault_and_a_bad_newsroom():
    bare = Path(tempfile.mkdtemp(prefix="tt-novault-"))
    try:
        ox.export(bare, "zz-test-obs")
        raise AssertionError("exported into a folder without .obsidian/")
    except SystemExit:
        pass
    assert list(bare.iterdir()) == []
    shutil.rmtree(bare)
    for bad in ("../x", "Alpha", ""):
        try:
            ox.export(VAULT, bad)
            raise AssertionError(f"accepted newsroom {bad!r}")
        except ValueError:
            pass


def test_missing_graph_gives_an_empty_index():
    w, _ = ox.export(VAULT, "zz-test-obs-none")
    empty = VAULT / "TieTrace" / "zz-test-obs-none"
    assert w == 1 and "no memory yet" in (empty / "TieTrace index.md").read_text(encoding="utf-8")
    assert "newsroom:zz-test-obs-none" not in DB.list_graphs()  # reading did not create it


# ---------- Reviewer's tests (Oct 16): hostile input, deletion safety, row cap, watch loop ----------
# Each uses its own scratch newsroom zz-test-obs-r* and its own temp vault; cleanup() removes them.

XNR = "zz-test-obs-rx"  # hostile story texts


def fresh_vault():
    v = Path(tempfile.mkdtemp(prefix="tt-vault-r-"))
    (v / ".obsidian").mkdir()
    return v


def hostile_cases():
    """Open cases in XNR with story/company text that tries to escape the folder or break YAML/links."""
    if "x" in S:
        return S["x"]
    memory.use_newsroom(XNR)
    stories = ["../../../../tmp/tt-evil", "/etc/passwd", "C:\\Windows\\evil", "CON", "...", " . ",
               "a|b#c^d[e]f]]g [[Fake link]]", 'quote " back\\slash\n---\ntietrace: false\nnext line',
               "日本語の記事" * 300]  # 1800 characters, 3 bytes each
    ids = []
    for s in stories:
        ids.append(ok(server.open_case(s, 'Coca-Cola "Co"\n---\nx: 1', "obesity: sugar", [HILL]))["case_id"])
    S["x"] = (fresh_vault(), ids)
    return S["x"]


def frontmatter(text):
    lines = text.split("\n")
    assert lines[0] == "---", lines[:2]
    end = lines.index("---", 1)
    out = {}
    for line in lines[1:end]:
        k, _, v = line.partition(": ")
        assert re.fullmatch(r"[a-z_]+", k), line
        out[k] = json.loads(v)  # every value is one JSON (= YAML) scalar or list on one line
    return out


def test_hostile_story_text_stays_inside_the_folder():
    vault, ids = hostile_cases()
    ox.export(vault, XNR)
    folder = vault / "TieTrace" / XNR
    for p in vault.rglob("*"):
        assert p.parts[:len(folder.parts)] == folder.parts or p.is_relative_to(vault / ".obsidian") \
            or p in (vault / "TieTrace", folder), f"wrote outside the folder: {p}"
    assert not Path("/tmp/tt-evil.md").exists()
    for p in folder.iterdir():
        assert len(p.name.encode()) <= 255 and "/" not in p.name and "\\" not in p.name, p.name
        assert not p.name.upper().startswith(("CON.", "PRN.", "AUX.", "NUL.")), p.name
    cases = [p for p in folder.glob("Case - *.md")]
    assert len(cases) == len(ids), sorted(p.name for p in cases)  # no two stories collapsed into one file
    # Round 2: make a removal happen too; the only write outside TieTrace/<id>/ may be into <vault>/.trash/.
    (folder / "Gone.md").write_text(ox.note({"type": "paper"}, "# stale"), encoding="utf-8")
    assert ox.export(vault, XNR) == (0, 1)
    for p in vault.rglob("*"):
        assert p.is_relative_to(folder) or p.is_relative_to(vault / ".obsidian") or p == vault / "TieTrace" \
            or p == vault / ".trash" or p.parent == vault / ".trash", f"wrote outside the folder: {p}"
    assert [p.name for p in (vault / ".trash").iterdir()] == ["Gone.md"]


def test_hostile_text_gives_valid_frontmatter_and_links():
    vault, ids = hostile_cases()
    folder = vault / "TieTrace" / XNR
    n = {p.name: p.read_text(encoding="utf-8") for p in folder.glob("*.md")}
    seen = set()
    for name, text in n.items():
        if name == "TieTrace README.md":
            continue
        fm = frontmatter(text)
        assert fm["tietrace"] is True, name
        if fm["type"] == "case":
            assert fm["company"] == 'Coca-Cola "Co"\n---\nx: 1' and fm["topic"] == "obesity: sugar", fm
            seen.add(fm["case_id"])
    assert seen == set(ids)
    for text in n.values():
        for target, alias in re.findall(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]", text):
            if target == "Fake link":
                continue  # the story text itself (journalist's words) contains this link; see review
            assert f"{target}.md" in n, f"dangling link {target!r}"
            assert "|" not in alias and "[" not in alias, alias
    assert ox.export(vault, XNR) == (0, 0)  # idempotent with hostile text too


def test_control_characters_never_reach_file_names():
    # Windows (the team's machine) refuses \x00-\x1f in file names: one pasted story would stop every export.
    memory.use_newsroom(XNR)
    ok(server.open_case("Soda\x0btax\x1bstory\x7fend", "Coca-Cola", "obesity", [HILL]))
    vault = fresh_vault()
    try:
        ox.export(vault, XNR)
        bad = [p.name for p in (vault / "TieTrace" / XNR).iterdir() if re.search(r"[\x00-\x1f\x7f]", p.name)]
        assert not bad, f"control characters in file names: {bad!r}"
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def test_user_note_with_an_exporter_name_is_never_overwritten():
    # README: "Notes without the `tietrace: true` line ... are never touched."
    vault = fresh_vault()
    try:
        folder = vault / "TieTrace" / XNR
        folder.mkdir(parents=True)
        mine = "# my own notes on this expert\n"
        (folder / f"{HILL}.md").write_text(mine, encoding="utf-8")
        (folder / "TieTrace index.md").write_text(mine, encoding="utf-8")
        hostile_cases()
        ox.export(vault, XNR)
        assert (folder / f"{HILL}.md").read_text(encoding="utf-8") == mine, "user note overwritten"
        assert (folder / "TieTrace index.md").read_text(encoding="utf-8") == mine, "user index overwritten"
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def test_deletion_never_follows_symlinks_or_touches_unmarked_or_odd_files():
    vault = fresh_vault()
    try:
        hostile_cases()
        ox.export(vault, XNR)
        folder = vault / "TieTrace" / XNR
        target = vault / "Owned elsewhere.md"
        target.write_text(ox.note({"type": "paper"}, "# a marked note outside the folder"), encoding="utf-8")
        linked = try_symlink(folder / "Link.md", target)
        (folder / "Starts with marker text.md").write_text("tietrace: true\n# not frontmatter\n", encoding="utf-8")
        (folder / "Sub.md").mkdir()  # a folder whose name ends in .md
        (folder / "Sub.md" / "Inner.md").write_text(ox.note({}, "nested"), encoding="utf-8")
        ox.export(vault, XNR)
        assert target.exists() and target.read_text(encoding="utf-8").startswith(ox.MARKER)
        assert not linked or (folder / "Link.md").is_symlink(), "a symlink note was deleted"
        assert (folder / "Starts with marker text.md").exists() and (folder / "Sub.md" / "Inner.md").exists()
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def test_a_non_utf8_user_file_does_not_stop_the_export():
    vault = fresh_vault()
    try:
        hostile_cases()
        folder = vault / "TieTrace" / XNR
        folder.mkdir(parents=True)
        (folder / "Notepad note.md").write_bytes("caf\xe9 notes\n".encode("cp1252"))
        w, _ = ox.export(vault, XNR)
        assert w > 0 and (folder / "Notepad note.md").exists()
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def test_row_cap_is_an_error_not_a_silent_cut():
    # 5001 cases: the export must stop with ROW_LIMIT (and delete nothing), never drop the newest cases.
    big = "zz-test-obs-rbig"
    DB.select_graph(f"newsroom:{big}").query(
        "UNWIND range(1, 5001) AS i CREATE (:Case {id: 'case_' + toString(i), story: 'synthetic test case ' + "
        "toString(i), company: 'X', topic: 'y', expert_ids: ['A1'], public_case: false, research_case: 'z', "
        "created_at: toString(10000 + i)})")  # synthetic fixture rows, test only
    vault = fresh_vault()
    try:
        try:
            ox.export(vault, big)
            got = len(list((vault / "TieTrace" / big).glob("Case - *.md")))
            raise AssertionError(f"no ROW_LIMIT: exported {got} of 5001 cases silently")
        except memory.ToolError as e:
            assert e.code == "ROW_LIMIT", e.code
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def start_watch(args, **kw):
    """Start the watch loop in a child process that ctrl_c() can stop (Windows has no SIGINT for a child)."""
    import subprocess
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # needed to send CTRL_BREAK_EVENT
    return subprocess.Popen([sys.executable, ox.__file__, *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, **kw)


def try_symlink(link, target, **kw):
    """Make a symlink, or (Windows only, without Developer Mode) return False with a message. Elsewhere a failure
    is an error: the symlink checks must never be skipped quietly on Linux or macOS."""
    try:
        link.symlink_to(target, **kw)
        return True
    except (OSError, NotImplementedError) as e:
        if os.name != "nt":
            raise
        print(f"      note: symlink sub-step skipped, Windows refused a symlink here ({type(e).__name__}: {e})")
        return False


def link_folder(link, target):
    """A folder link: a symlink, or on Windows without symlink rights a directory junction (needs no rights)."""
    if not try_symlink(link, target, target_is_directory=True):
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def ctrl_c(p):
    import signal
    p.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)


def test_watch_retries_on_db_errors_and_stops_cleanly_on_ctrl_c():
    vault = fresh_vault()
    try:
        env = {**os.environ, "FALKORDB_PORT": "1"}  # nothing listens there: every export is a DB error
        p = start_watch(["--vault", str(vault), "--newsroom", XNR, "--watch", "0.2"], env=env, cwd=ROOT)
        time.sleep(3)
        assert p.poll() is None, f"watch loop died: {p.stdout.read()}"
        ctrl_c(p)
        out, _ = p.communicate(timeout=10)
        assert p.returncode == 0 and "retrying" in out and out.rstrip().endswith("stopped"), (p.returncode, out)
        assert "Traceback" not in out, out
    finally:
        shutil.rmtree(vault, ignore_errors=True)



def test_watch_survives_a_file_error_and_loses_nothing():
    # `.trash` is a file, so moving a stale note fails with an OSError: the watch loop must retry, not die.
    hostile_cases()
    vault = fresh_vault()
    try:
        (vault / ".trash").write_text("not a folder", encoding="utf-8")
        folder = vault / "TieTrace" / XNR
        folder.mkdir(parents=True)
        (folder / "Old.md").write_text(ox.note({"type": "paper"}, "# stale"), encoding="utf-8")
        p = start_watch(["--vault", str(vault), "--newsroom", XNR, "--watch", "0.2"], cwd=ROOT)
        time.sleep(4)
        assert p.poll() is None, f"watch loop died: {p.stdout.read()}"
        ctrl_c(p)
        out, _ = p.communicate(timeout=10)
        assert p.returncode == 0 and "Traceback" not in out, (p.returncode, out)
        assert (folder / "Old.md").exists() and (vault / ".trash").read_text(encoding="utf-8") == "not a folder"
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def test_trash_move_never_clobbers_a_file():
    vault = fresh_vault()
    real_time = ox.time.time
    try:
        hostile_cases()
        folder = vault / "TieTrace" / XNR
        folder.mkdir(parents=True)
        trash = vault / ".trash"
        trash.mkdir()
        (trash / "Old.md").write_text("mine 1", encoding="utf-8")
        (trash / "Old 1000.md").write_text("mine 2", encoding="utf-8")
        (folder / "Old.md").write_text(ox.note({"type": "paper"}, "# stale"), encoding="utf-8")
        ox.time.time = lambda: 1.0  # the clock the exporter uses for a second name: "Old 1000.md"
        ox.export(vault, XNR)
        assert (trash / "Old.md").read_text(encoding="utf-8") == "mine 1"
        assert (trash / "Old 1000.md").read_text(encoding="utf-8") == "mine 2", "a file in .trash was overwritten"
        assert sum(1 for p in trash.iterdir() if p.read_text(encoding="utf-8").startswith(ox.MARKER)) == 1
    finally:
        ox.time.time = real_time
        shutil.rmtree(vault, ignore_errors=True)


def test_trash_move_never_leaves_the_vault_through_a_symlink():
    vault, outside = fresh_vault(), Path(tempfile.mkdtemp(prefix="tt-outside-"))
    try:
        hostile_cases()
        folder = vault / "TieTrace" / XNR
        folder.mkdir(parents=True)
        link_folder(vault / ".trash", outside)
        (folder / "Old.md").write_text(ox.note({"type": "paper"}, "# stale"), encoding="utf-8")
        try:
            ox.export(vault, XNR)
        except OSError:
            pass  # refusing is fine; moving the note out of the vault is not
        assert list(outside.iterdir()) == [], f"moved outside the vault: {list(outside.iterdir())}"
    finally:
        shutil.rmtree(vault, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_a_linked_export_folder_is_refused():
    """The TieTrace folder or the newsroom folder being a link (symlink or junction) would send every note, and
    every removal, outside the vault: the export must refuse and write nothing there."""
    for linked in ("TieTrace", f"TieTrace/{XNR}"):
        vault, outside = fresh_vault(), Path(tempfile.mkdtemp(prefix="tt-outside-"))
        try:
            hostile_cases()
            if linked != "TieTrace":
                (vault / "TieTrace").mkdir()
            link_folder(vault / linked, outside)
            (outside / "Old.md").write_text(ox.note({"type": "paper"}, "# stale"), encoding="utf-8")
            try:
                ox.export(vault, XNR)
                raise AssertionError(f"exported through a linked {linked}")
            except SystemExit as e:
                assert "is a link" in str(e), e
            assert [p.name for p in outside.iterdir()] == ["Old.md"], list(outside.iterdir())
            assert not (vault / ".trash").exists()
        finally:
            shutil.rmtree(vault, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


def test_an_empty_newsroom_does_not_remove_many_notes():
    vault = fresh_vault()
    try:
        folder = vault / "TieTrace" / "zz-test-obs-rempty"
        folder.mkdir(parents=True)
        for i in range(11):
            (folder / f"Old {i}.md").write_text(ox.note({"type": "paper"}, "# stale"), encoding="utf-8")
        assert ox.export(vault, "zz-test-obs-rempty")[1] == 0
        assert len(list(folder.glob("Old *.md"))) == 11 and not (vault / ".trash").exists()
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def test_reading_restores_the_memory_module_state():
    memory.use_newsroom(XNR)
    before = memory.GRAPH, memory.SESSION_ID
    vault = fresh_vault()
    try:
        ox.export(vault, "zz-test-obs-rempty")
        assert (memory.GRAPH, memory.SESSION_ID) == before
    finally:
        shutil.rmtree(vault, ignore_errors=True)



def test_paper_names_never_share_a_file_even_ignoring_case():
    names = ox.paper_names({"10.1/AB", "10.1/ab", "10.1/a((b", "10.1/a_b", "10.1016/j.x.2020.01"})
    assert len({n.lower() for n in names.values()}) == 5, names  # Windows/macOS ignore case in file names
    assert names["10.1016/j.x.2020.01"] == "Paper 10.1016_j.x.2020.01", names  # no clash: plain readable name
    for n in names.values():
        assert ox.safe(n) == n and len(n.encode()) < 200, n
    assert ox.paper_names({"10.1/AB", "10.1/ab"}) == ox.paper_names({"10.1/ab", "10.1/AB"})  # stable


def cleanup():
    for g in DB.list_graphs():
        if g == NR or g.startswith("newsroom:zz-test-obs-r"):
            DB.select_graph(g).delete()
    if "x" in S:
        shutil.rmtree(S["x"][0], ignore_errors=True)


if __name__ == "__main__":
    only = sys.argv[1:]
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)
             and (not only or any(o in n for o in only))]
    cleanup()
    memory.init()
    failed = 0
    try:
        setup()
        for name, fn in tests:
            t0 = time.time()
            try:
                fn()
                print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
            except Exception as e:
                failed += 1
                lines = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
                print(f"FAIL  {name}  (line {lines[-1] if lines else '?'}): {str(e)[:500] or type(e).__name__}")
    finally:
        cleanup()
        shutil.rmtree(VAULT, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
