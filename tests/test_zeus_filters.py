"""Test per i filtri template ZEUS (apps.companies.templatetags.zeus_filters).

Il filtro markdownify riceve spesso output LLM (contenuti DNA, risposte della
guida): i link markdown devono diventare <a> solo con URL sicuri — niente
javascript:/data: (XSS via prompt injection) e niente break-out dagli attributi
via virgolette.
"""

import pytest

from apps.companies.templatetags.zeus_filters import markdownify


class TestMarkdownifyBasics:
    def test_bold_italic_code(self):
        html = str(markdownify("**grassetto** e *corsivo* e `codice`"))
        assert "<strong>grassetto</strong>" in html
        assert "<em>corsivo</em>" in html
        assert "<code>codice</code>" in html

    def test_empty_input(self):
        assert markdownify("") == ""


class TestMarkdownifyLinkSafety:
    def test_https_link_renders(self):
        html = str(markdownify("[sito](https://example.com/pagina)"))
        assert '<a href="https://example.com/pagina" rel="noopener noreferrer">sito</a>' in html

    def test_http_mailto_relative_and_anchor_links_render(self):
        for url in ("http://example.com", "mailto:info@example.com", "/percorso", "#ancora"):
            html = str(markdownify(f"[testo]({url})"))
            assert f'<a href="{url}"' in html

    def test_javascript_link_is_neutralised(self):
        html = str(markdownify("[clicca](javascript:alert(1))"))
        assert "<a" not in html
        assert "javascript:" not in html
        assert "clicca" in html  # il testo resta, il link no

    def test_scheme_check_is_case_insensitive(self):
        html = str(markdownify("[clicca](JaVaScRiPt:alert(1))"))
        assert "<a" not in html

    def test_data_url_link_is_neutralised(self):
        html = str(markdownify("[clicca](data:text/html;base64,PHNjcmlwdD4=)"))
        assert "<a" not in html

    def test_url_with_quote_cannot_break_out_of_href(self):
        # Senza escape preliminare (uso del filtro su contenuti DNA) una "
        # nell'URL chiuderebbe l'attributo href e inietterebbe attributi.
        html = str(markdownify('[x](https://a.b/"onclick="alert(1)")'))
        assert "<a" not in html
        assert "onclick" not in html

    def test_url_with_spaces_is_not_linked(self):
        html = str(markdownify("[x](https://a.b/c d)"))
        assert "<a" not in html
