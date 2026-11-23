import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from description_sanitizer import sanitize_description  # noqa: E402


class SanitizeDescriptionTests(unittest.TestCase):
    def test_strips_attributes_and_unwraps_spans(self):
        clean, _ = sanitize_description(
            '<p style="color:red" class="x"><span style="font-size:14px">Merhaba <b class="y">dünya</b></span></p>'
        )
        self.assertEqual(clean, "<p>Merhaba <b>dünya</b></p>")
        for forbidden in ("style=", "class=", "<span"):
            self.assertNotIn(forbidden, clean)

    def test_removes_phone_lines_in_all_turkish_formats(self):
        raw = (
            "<p>Satılık fabrika<br>Bilgi için: 0 (538) 891 91 11<br>Son satır</p>"
            "<ul><li>+90 538 891 91 11</li><li>Trafo 630 kVA</li></ul>"
            "<p>05388919111</p><div>Ofis: 0232 362 19 19</div>"
        )
        clean, removed = sanitize_description(raw)
        self.assertEqual(removed, 4)
        self.assertEqual(clean, "<p>Satılık fabrika<br>Son satır</p>\n<ul><li>Trafo 630 kVA</li></ul>")

    def test_prices_and_areas_are_not_mistaken_for_phones(self):
        clean, removed = sanitize_description("<p>Fiyat 159.000.000 TL, 18.159 m², ada 6.805</p>")
        self.assertEqual(removed, 0)
        self.assertIn("159.000.000", clean)

    def test_drops_script_style_iframe_with_content(self):
        clean, _ = sanitize_description(
            "<p>Önce</p><script>alert('x')</script><style>p{color:red}</style>"
            "<iframe src=https://evil.example>gizli</iframe><p>Sonra</p>"
        )
        self.assertEqual(clean, "<p>Önce</p>\n<p>Sonra</p>")

    def test_nbsp_runs_collapse_and_empty_paragraphs_disappear(self):
        clean, _ = sanitize_description("<p>&nbsp;&nbsp; Arsa&nbsp;&nbsp;alanı</p><p>&nbsp;</p><p><b> </b></p>")
        self.assertEqual(clean, "<p>Arsa alanı</p>")

    def test_div_is_a_paragraph_boundary_and_text_is_escaped(self):
        clean, _ = sanitize_description('<div>Bir</div><div>İki &lt;img onerror=x&gt;</div><a href="j">link</a>')
        self.assertEqual(clean, "<p>Bir</p>\n<p>İki &lt;img onerror=x&gt;</p>\n<p>link</p>")

    def test_formatting_stays_balanced_when_a_line_is_dropped(self):
        clean, removed = sanitize_description("<p><b>Başlık<br>0538 891 91 11<br>devam</b></p>")
        self.assertEqual(removed, 1)
        self.assertEqual(clean, "<p><b>Başlık</b><br><b>devam</b></p>")

    def test_nested_list_keeps_trailing_text_inside_outer_item(self):
        clean, _ = sanitize_description("<ul><li>a<ul><li>b</li></ul>c</li></ul>")
        self.assertEqual(clean, "<ul><li>a<ul><li>b</li></ul>c</li></ul>")

    def test_bare_list_item_then_paragraph_keeps_order(self):
        clean, _ = sanitize_description("<li>x</li><p>y</p>")
        self.assertEqual(clean, "<ul><li>x</li></ul>\n<p>y</p>")

    def test_unclosed_list_items_become_siblings(self):
        clean, _ = sanitize_description("<p>önce</p><ol><li>bir<li>iki</ol><p>sonra</p>")
        self.assertEqual(clean, "<p>önce</p>\n<ol><li>bir</li><li>iki</li></ol>\n<p>sonra</p>")


if __name__ == "__main__":
    unittest.main()
