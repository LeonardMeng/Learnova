import pytest

from learnova.ignore import TEMPLATE, IgnoreFileError, load, parse

ID = "3e6135e3-ecf0-4389-846c-e4126df126b0"
OTHER = "11111111-1111-1111-1111-111111111111"


def ignored(rules_text: str, *path: str, page_id: str = OTHER) -> bool:
    return parse(rules_text).ignores(tuple(path), page_id)


def test_title_at_any_depth_and_case():
    rules = "Behaviour Questions"
    assert ignored(rules, "Behaviour Questions")
    assert ignored(rules, "Database", "behaviour questions")
    assert not ignored(rules, "Behaviour Questions 2")


def test_globs():
    assert ignored("Draft*", "Redis", "Draft – persistence")
    assert ignored("Week ?", "Week 3")
    assert not ignored("Week ?", "Week 10")


def test_paths_are_anchored_at_the_top_page():
    rules = "Database / Old notes"
    assert ignored(rules, "Database", "Old notes")
    assert ignored(rules, "database", "OLD NOTES")
    assert not ignored(rules, "Old notes")
    assert not ignored(rules, "Archive", "Database", "Old notes")
    assert ignored("/Old notes", "Old notes") and not ignored("/Old notes", "X", "Old notes")


def test_double_star():
    assert ignored("**/Scratch", "Scratch")
    assert ignored("**/Scratch", "A", "B", "Scratch")
    assert ignored("Redis/**/Tmp", "Redis", "Tmp") and ignored("Redis/**/Tmp", "Redis", "x", "Tmp")
    assert not ignored("Redis/**/Tmp", "Other", "Tmp")


def test_ids_and_urls():
    for line in (
        ID,
        ID.replace("-", ""),
        f"https://app.notion.com/p/Behaviour-{ID.replace('-', '')}?source=copy_link",
    ):
        assert ignored(line, "Renamed page", page_id=ID)
        assert not ignored(line, "Renamed page", page_id=OTHER)
    # a title that merely contains hex isn't treated as an ID
    assert ignored("Notes 3e6135e3", "Notes 3e6135e3")


def test_negation_last_match_wins_and_comments():
    rules = """
    # drafts are noise
    Draft*
    !Draft – final
    """
    assert ignored(rules, "Draft one")
    assert not ignored(rules, "Draft – final")
    assert ignored("!Draft – final\nDraft*", "Draft – final")  # order matters


def test_top_page_is_never_ignored():
    assert not parse("*").ignores((), ID)
    assert parse("*").ignores(("Anything",), OTHER)


@pytest.mark.parametrize(
    "text, message",
    [("ok\n!", "line 2: nothing after '!'"), ("a//b", "line 1: 'a//b' has an empty title"),
     ("\n\n**", "line 3: '\\*\\*' alone")],
)  # fmt: skip
def test_errors_name_the_line(text, message):
    with pytest.raises(IgnoreFileError, match=message):
        parse(text)


def test_load_and_template(tmp_path):
    assert not load(tmp_path)  # no file: nothing ignored
    (tmp_path / ".learnovaignore").write_text(TEMPLATE + "Scratch\n")
    rules = load(tmp_path)
    assert len(rules.rules) == 1 and rules.ignores(("Scratch",), OTHER)
    assert not parse(TEMPLATE)  # the template is all comments
