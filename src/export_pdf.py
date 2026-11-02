"""
Riassunto .md -> PDF: Markdown -> HTML (stessa conversione della dashboard) -> xhtml2pdf.
Solo Python, nessun programma esterno da installare.
"""

import io
from pathlib import Path

from xhtml2pdf import pisa

from sessioni import md_in_html

# Helvetica (predefinito di xhtml2pdf) non ha frecce e simboli usati nei riassunti (→):
# si usa un font di sistema se c'è: Arial su Windows; su Linux (Fedora) Liberation Sans o DejaVu Sans.
FAMIGLIE = [("arial.ttf", "arialbd.ttf", "ariali.ttf"),
            ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"),
            ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf")]
RADICI = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
          Path.home() / ".local/share/fonts", Path.home() / ".fonts"]


def _trova_font():
    """(cartella, {normal, bold, italic}) di una famiglia completa nella stessa cartella, altrimenti (None, {})."""
    for nomi in FAMIGLIE:
        for radice in RADICI:
            if not radice.is_dir():
                continue
            for trovato in radice.rglob(nomi[0]):
                if all((trovato.parent / n).exists() for n in nomi):
                    return trovato.parent, dict(zip(("normal", "bold", "italic"), nomi))
    return None, {}


FONT_DIR, FONT = _trova_font()

# xhtml2pdf recente legge solo i file accanto al documento: gli si apre la sola cartella dei font, niente rete
try:
    from xhtml2pdf.config.resources import ResourceAccessPolicy
    POLITICA = ({"resource_policy": ResourceAccessPolicy(allow_remote=False, base_dir=FONT_DIR)}
                if FONT_DIR else {})
except ImportError:
    POLITICA = {}

CSS = """
@page { size: a4; margin: 2cm 1.8cm 2cm 1.8cm;
        @frame footer { -pdf-frame-content: piede; bottom: 1cm; margin-left: 1.8cm; margin-right: 1.8cm; height: 1cm; } }
body { font-family: %(font)s; font-size: 10.5pt; line-height: 1.45; color: #2A2D33; }
h1 { font-size: 20pt; color: #0F1115; margin: 0 0 4pt 0; padding-bottom: 6pt; border-bottom: 2pt solid #540409; }
h2 { font-size: 15pt; color: #540409; margin: 18pt 0 6pt 0; }
h3 { font-size: 12.5pt; color: #0F1115; margin: 12pt 0 4pt 0; }
h4 { font-size: 11pt; color: #0F1115; margin: 8pt 0 3pt 0; }
p, li { margin: 0 0 4pt 0; }
ul, ol { margin: 0 0 6pt 0; }
strong { color: #0F1115; }
table { border: 0.5pt solid #DFE1E5; margin: 4pt 0 8pt 0; }
th { background-color: #F1F3F5; padding: 4pt; text-align: left; font-weight: bold; }
td { padding: 4pt; border-top: 0.5pt solid #EDEEF0; vertical-align: top; }
hr { color: #DFE1E5; }
code { font-family: Courier; font-size: 9.5pt; }
.incerto { color: #C05621; font-weight: bold; }
#piede { font-size: 8.5pt; color: #5A5E66; text-align: right; }
"""


def _css() -> str:
    if FONT_DIR:
        facce = "".join(
            f"@font-face {{ font-family: Testo; src: url('{(FONT_DIR / f).as_posix()}'); "
            f"{'font-weight: bold;' if stile == 'bold' else ''}{'font-style: italic;' if stile == 'italic' else ''} }}\n"
            for stile, f in FONT.items())
        return facce + CSS % {"font": "Testo"}
    return CSS % {"font": "Helvetica"}


def markdown_in_pdf(testo_md: str, titolo: str) -> bytes:
    corpo = md_in_html(testo_md).replace('<mark class="incerto">INCERTO</mark>',
                                         '<span class="incerto">[INCERTO]</span>')
    pagina = (f"<html><head><meta charset='utf-8'><title>{titolo}</title><style>{_css()}</style></head>"
              f"<body>{corpo}<div id='piede'>pagina <pdf:pagenumber> di <pdf:pagecount></div></body></html>")
    out = io.BytesIO()
    esito = pisa.CreatePDF(pagina, dest=out, encoding="utf-8", **POLITICA)
    if esito.err:
        raise RuntimeError(f"conversione PDF non riuscita ({esito.err} errori)")
    return out.getvalue()
