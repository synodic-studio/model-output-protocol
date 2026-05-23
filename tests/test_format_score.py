"""Tests for format score — composite message shape penalty."""

import mop.format_score as fs


class TestWrapPenalty:
    def test_empty(self):
        assert fs.wrap_penalty("") == 0.0

    def test_short_lines(self):
        """Lines under prose_width get zero penalty."""
        text = "\n".join(["short"] * 5)
        assert fs.wrap_penalty(text) == 0.0

    def test_single_wrap_line(self):
        """A line that wraps to 2 rendered lines gets 0.5 * (2-1)^2 = 0.5."""
        text = "x" * (fs.PROSE_LINE_WIDTH + 1)  # one char over
        assert fs.wrap_penalty(text) == 0.5

    def test_double_wrap_line(self):
        """A line that wraps to 3 rendered lines gets 0.5 * (3-1)^2 = 2.0."""
        text = "x" * (fs.PROSE_LINE_WIDTH * 2 + 1)
        assert fs.wrap_penalty(text) == 2.0

    def test_code_fences_exempt(self):
        """Lines inside ``` are still measured against mono_width."""
        text = "```\n" + "x" * (fs.MONO_LINE_WIDTH + 1) + "\n```"
        assert fs.wrap_penalty(text) == 0.5  # only the content line wraps

    def test_multiple_long_lines_stack(self):
        text = ("x" * (fs.PROSE_LINE_WIDTH + 1) + "\n") * 3
        text = text.rstrip("\n")
        assert fs.wrap_penalty(text) == 1.5  # 3 lines * 0.5 each


class TestTotalRenderedLines:
    def test_empty(self):
        assert fs.total_rendered_lines("") == 0

    def test_short_lines(self):
        assert fs.total_rendered_lines("hello\nworld") == 2

    def test_long_line_wraps(self):
        text = "x" * (fs.PROSE_LINE_WIDTH * 2 + 1)
        assert fs.total_rendered_lines(text) == 3

    def test_code_block_fences_count(self):
        text = "```\nhello\n```"
        # ``` (1) + hello (1) + ``` (1) = 3
        assert fs.total_rendered_lines(text) == 3


class TestHeightPenalty:
    def test_empty(self):
        assert fs.height_penalty("") == 0.0

    def test_under_free_lines(self):
        """Messages under half-budget incur zero penalty."""
        text = "\n".join(["short"] * 10)
        assert fs.height_penalty(text) == 0.0

    def test_linear_region(self):
        """Messages between half-budget and budget get linear penalty."""
        # HEIGHT_FREE_LINES = budget // 2 = 16
        # 18 rendered lines → (18-16) * 0.5 = 1.0
        text = "\n".join(["short"] * 18)
        assert fs.height_penalty(text, budget=32) == 1.0

    def test_cliff_region(self):
        """Messages over budget get quadratic cliff."""
        # 34 rendered lines, budget=32
        # cliff_base = (32-16) * 0.5 = 8
        # over = 10.0 * (34-32)^2 = 40
        # total = 48
        text = "\n".join(["short"] * 34)
        assert fs.height_penalty(text, budget=32) == 48.0


class TestStructurePenalty:
    def test_empty(self):
        assert fs.structure_penalty("") == 0.0

    def test_few_prose_lines_free(self):
        """Up to PROSE_FREE_LINES prose lines incur no penalty."""
        text = "\n".join(["prose line"] * fs.PROSE_FREE_LINES)
        assert fs.structure_penalty(text) == 0.0

    def test_excess_prose_lines(self):
        """Prose lines beyond free allowance incur 1 point each."""
        text = "\n".join(["prose line"] * (fs.PROSE_FREE_LINES + 3))
        assert fs.structure_penalty(text) == 3.0

    def test_list_items_exempt(self):
        text = "- list item\n" * 10
        assert fs.structure_penalty(text) == 0.0

    def test_headers_exempt(self):
        text = "# Header\n## Subheader\nprose line"
        assert fs.structure_penalty(text) == 0.0  # 1 prose, within free allowance

    def test_code_fences_exempt(self):
        text = "```\ncode line\ncode line\n```"
        assert fs.structure_penalty(text) == 0.0

    def test_blank_lines_exempt(self):
        text = "prose\n\nmore prose\n\n\nlast prose"
        assert fs.structure_penalty(text) == 0.0  # 3 prose, within free allowance

    def test_mixed_content(self):
        """Mix of structured and prose lines, only excess prose is penalized."""
        text = (
            "# Header\n"
            + "prose line\n" * (fs.PROSE_FREE_LINES + 2)
            + "- list\n"
            + "- list\n"
            + "more prose\n"
        )
        # 6 prose lines (after PROSE_FREE_LINES=4) → penalty 2.0
        # plus 1 more prose → penalty 3.0
        assert fs.structure_penalty(text) == 3.0


class TestFormatScore:
    def test_empty(self):
        result = fs.format_score("")
        assert result["format_score"] == 0.0

    def test_short_message(self):
        """A clean short message should score near zero."""
        result = fs.format_score("The build passed. Tests are green.")
        assert result["format_score"] == 0.0

    def test_returns_expected_keys(self):
        result = fs.format_score("hello world")
        expected_keys = {"wrap_penalty", "height_penalty", "structure_penalty",
                          "format_score", "rendered_lines"}
        assert set(result.keys()) == expected_keys
