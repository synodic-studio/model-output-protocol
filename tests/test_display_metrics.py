"""Tests for display metrics — pure function measurements."""

import mop.display_metrics as dm


class TestWordCount:
    def test_empty(self):
        assert dm.word_count("") == 0

    def test_single_word(self):
        assert dm.word_count("hello") == 1

    def test_multiple_words(self):
        assert dm.word_count("the quick brown fox") == 4

    def test_extra_whitespace(self):
        assert dm.word_count("  hello   world  ") == 2


class TestCharCount:
    def test_empty(self):
        assert dm.char_count("") == 0

    def test_basic(self):
        assert dm.char_count("hello") == 5


class TestLineCount:
    def test_empty(self):
        assert dm.line_count("") == 0

    def test_single_line_no_newline(self):
        assert dm.line_count("hello") == 1

    def test_two_lines(self):
        assert dm.line_count("hello\nworld") == 2

    def test_empty_lines(self):
        assert dm.line_count("hello\n\nworld") == 3

    def test_trailing_newline(self):
        assert dm.line_count("hello\n") == 1


class TestMaxLineWidth:
    def test_empty(self):
        assert dm.max_line_width("") == 0

    def test_single_line(self):
        assert dm.max_line_width("hello") == 5

    def test_longest_line(self):
        assert dm.max_line_width("a\nlonger line here\nc") == 16

    def test_empty_lines(self):
        assert dm.max_line_width("a\n\nc") == 1


class TestRenderedLineCount:
    def test_empty(self):
        assert dm.rendered_line_count("") == 0

    def test_short_line_no_wrap(self):
        assert dm.rendered_line_count("short") == 1

    def test_long_line_wraps(self):
        """A 70-char line at prose_width=35 wraps to 2 rendered lines."""
        text = "x" * 70
        assert dm.rendered_line_count(text, prose_width=35) == 2

    def test_long_line_three_wraps(self):
        """A 100-char line at prose_width=35 wraps to 3 rendered lines."""
        text = "x" * 100
        assert dm.rendered_line_count(text, prose_width=35) == 3

    def test_code_block_uses_mono_width(self):
        """Lines inside ``` use mono_width instead of prose_width."""
        text = "```\n" + "x" * 110 + "\n```"
        # 110 chars at mono_width=37 → ceil(110/37) = 3 lines
        assert dm.rendered_line_count(text, mono_width=37) == 5  # ``` + wrap + wrap + wrap + ```

    def test_newlines_force_breaks(self):
        """Explicit newlines reset the wrap."""
        text = "a\nb\nc"
        assert dm.rendered_line_count(text) == 3

    def test_empty_line_counts_as_one(self):
        text = "hello\n\nworld"
        assert dm.rendered_line_count(text) == 3

    def test_mixed_code_and_prose(self):
        text = "short prose\n```\n" + "x" * 80 + "\n```\nmore prose"
        result = dm.rendered_line_count(text, prose_width=35, mono_width=37)
        # short prose (1) + ``` (1) + 80/37=3 + ``` (1) + more prose (1) = 7
        assert result == 7


class TestOverflowsScreen:
    def test_under_budget(self):
        text = "\n".join(["short"] * 20)
        assert not dm.overflows_screen(text, budget=32)

    def test_over_budget(self):
        text = "\n".join(["short"] * 40)
        assert dm.overflows_screen(text, budget=32)

    def test_empty_does_not_overflow(self):
        assert not dm.overflows_screen("")


class TestListItemCount:
    def test_no_items(self):
        assert dm.list_item_count("plain prose") == 0

    def test_dash_items(self):
        assert dm.list_item_count("- one\n- two\n- three") == 3

    def test_star_items(self):
        assert dm.list_item_count("* a\n* b") == 2

    def test_numbered_items(self):
        assert dm.list_item_count("1. first\n2. second") == 2

    def test_not_confused_by_dash_in_prose(self):
        """A dash in the middle of a line is not a list item."""
        assert dm.list_item_count("this - is not a list") == 0

    def test_indented_items(self):
        assert dm.list_item_count("  - indented") == 1


class TestCodeBlockChars:
    def test_no_code(self):
        assert dm.code_block_chars("plain text") == 0

    def test_code_block(self):
        text = "```\ncode here\n```"
        assert dm.code_block_chars(text) == len("```\ncode here\n```")

    def test_multiple_blocks(self):
        text = "```a``` and ```b```"
        assert dm.code_block_chars(text) == 14  # ```a``` (7) + ```b``` (7)


class TestCodeBlockRatio:
    def test_empty(self):
        assert dm.code_block_ratio("") == 0.0

    def test_all_code(self):
        text = "```\ncode\n```"
        assert dm.code_block_ratio(text) == 1.0

    def test_partial_code(self):
        text = "prose\n```\ncode\n```\nprose"
        ratio = dm.code_block_ratio(text)
        assert 0.0 < ratio < 1.0


class TestEmDashCount:
    def test_no_dashes(self):
        assert dm.em_dash_count("hello world") == 0

    def test_one(self):
        assert dm.em_dash_count("hello—world") == 1


class TestUrlCount:
    def test_no_urls(self):
        assert dm.url_count("hello world") == 0

    def test_one(self):
        assert dm.url_count("see https://example.com") == 1

    def test_multiple(self):
        assert dm.url_count("https://a.com and https://b.com") == 2


class TestComputeAll:
    def test_returns_all_keys(self):
        result = dm.compute_all("hello world")
        expected_keys = {
            "word_count", "char_count", "line_count", "max_line_width",
            "rendered_line_count", "overflows_screen", "list_item_count",
            "code_block_chars", "code_block_ratio", "em_dash_count",
            "url_count",
        }
        assert set(result.keys()) == expected_keys

    def test_empty_text(self):
        result = dm.compute_all("")
        assert result["word_count"] == 0
        assert result["char_count"] == 0
        assert result["rendered_line_count"] == 0
        assert not result["overflows_screen"]
