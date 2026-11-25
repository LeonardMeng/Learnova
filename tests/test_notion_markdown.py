from collections import Counter

from learnova.sources.notion_markdown import MarkdownConverter, image_placeholder, rich_text


def rt(text, **ann):
    href = ann.pop("href", None)
    return {"type": "text", "plain_text": text, "annotations": ann, "href": href}


def blk(id, btype, children=None, **data):
    return {"id": id, "type": btype, "has_children": bool(children), btype: data}, children or []


def convert(*items):
    """items are (block, children) pairs; children may themselves be such pairs."""
    tree = {}

    def register(parent, pairs):
        tree[parent] = [b for b, _ in pairs]
        for b, kids in pairs:
            if kids:
                register(b["id"], kids)

    register("page", list(items))
    counts = Counter()
    md = MarkdownConverter(lambda bid: tree.get(bid, []), counts).page("page")
    return md, counts


def convert_with_images(*items):
    tree = {}

    def register(parent, pairs):
        tree[parent] = [b for b, _ in pairs]
        for b, kids in pairs:
            if kids:
                register(b["id"], kids)

    register("page", list(items))
    counts = Counter()
    converter = MarkdownConverter(lambda bid: tree.get(bid, []), counts)
    return converter.page("page"), counts, converter.images


def test_rich_text_annotations_and_links():
    out = rich_text(
        [
            rt("plain "),
            rt("bold ", bold=True),
            rt("it", italic=True),
            rt(" "),
            rt("x", code=True),
            rt(" "),
            rt("link", href="https://example.com"),
            {"type": "equation", "equation": {"expression": "E=mc^2"}},
        ]
    )
    assert out == "plain **bold** *it* `x` [link](https://example.com)$E=mc^2$"


def test_headings_paragraphs_and_lists():
    md, _ = convert(
        blk("h", "heading_2", rich_text=[rt("Title")]),
        blk("p", "paragraph", rich_text=[rt("Hello")]),
        blk("e", "paragraph", rich_text=[]),  # empty paragraphs are dropped
        blk("b1", "bulleted_list_item", [blk("n1", "numbered_list_item", rich_text=[rt("one")])],
            rich_text=[rt("a")]),
        blk("b2", "bulleted_list_item", rich_text=[rt("b")]),
        blk("t", "to_do", rich_text=[rt("done")], checked=True),
    )  # fmt: skip
    assert md == "## Title\n\nHello\n\n- a\n  1. one\n- b\n- [x] done\n"


def test_code_quote_toggle_table_equation_divider():
    md, _ = convert(
        blk("c", "code", rich_text=[rt("print(1)", bold=True)], language="python"),
        blk("q", "callout", [blk("qp", "paragraph", rich_text=[rt("more")])],
            rich_text=[rt("Note")], icon={"type": "emoji", "emoji": "💡"}),
        blk("tg", "toggle", [blk("tp", "paragraph", rich_text=[rt("hidden")])],
            rich_text=[rt("Click")]),
        blk("tb", "table", [
            blk("r1", "table_row", cells=[[rt("k")], [rt("v|x")]]),
            blk("r2", "table_row", cells=[[rt("a")], [rt("b")]]),
        ]),
        blk("eq", "equation", expression="a^2+b^2"),
        blk("d", "divider"),
    )  # fmt: skip
    assert "```python\nprint(1)\n```" in md  # code keeps raw text, no ** marks
    assert "> 💡 Note\n>\n> more" in md
    assert "<details>\n<summary>Click</summary>\n\nhidden\n\n</details>" in md
    assert "| k | v\\|x |\n| --- | --- |\n| a | b |" in md
    assert "$$\na^2+b^2\n$$" in md
    assert "\n---\n" in md


def test_pages_columns_placeholders_and_unknown_blocks():
    md, counts, images = convert_with_images(
        blk("cl", "column_list", [
            blk("c1", "column", [blk("p1", "paragraph", rich_text=[rt("left")])]),
            blk("c2", "column", [blk("p2", "paragraph", rich_text=[rt("right")])]),
        ]),
        blk("sub", "child_page", title="Persistence"),
        blk("db", "child_database", title="Cards"),
        blk("img", "image", caption=[rt("diagram")], type="file",
            file={"url": "https://s3.example/ws/file-1/tree.png?X-Amz-Signature=abc"}),
        blk("ext", "image", caption=[], type="external",
            external={"url": "https://example.com/pic.png?size=2"}),
        blk("nolink", "image", caption=[rt("lost")], type="file_upload", file_upload={"id": "u"}),
        blk("f", "pdf", name="slides.pdf"),
        blk("toc", "table_of_contents"),
        blk("w", "mystery_block"),
    )  # fmt: skip
    assert md.startswith("left\n\nright\n\n")
    assert "→ Sub-page: Persistence" in md
    assert "→ Database (not synced): Cards" in md
    assert f"{image_placeholder('img')}\n\n{image_placeholder('ext')}" in md
    assert "*[image not available: lost]*" in md
    assert [(i.block_id, i.source_key, i.caption) for i in images] == [
        ("img", "https://s3.example/ws/file-1/tree.png", "diagram"),  # signature dropped
        ("ext", "https://example.com/pic.png?size=2", ""),  # external links kept whole
    ]
    assert "[pdf attachment not supported in v1: slides.pdf]" in md
    assert "table_of_contents" not in md
    assert '<!-- unsupported: "mystery_block" -->' in md
    assert counts["image"] == 3 and counts["paragraph"] == 2 and counts["mystery_block"] == 1


def test_all_heading_levels():
    md, counts = convert(
        *(blk(f"h{n}", f"heading_{n}", rich_text=[rt(f"Level {n}")]) for n in range(1, 5))
    )
    assert md == "# Level 1\n\n## Level 2\n\n### Level 3\n\n#### Level 4\n"
