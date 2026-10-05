"""Export Open Recommender's shared film mark as production logo assets.

Run: python3 media/brand/export_logos.py
Uses installed Pillow and fontTools; SVG wordmarks have outlined lettering.
"""

from pathlib import Path
import importlib.util
import math
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

OUT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('launch', OUT.parent/'launch'/'render_launch.py')
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)
TYPE = TTFont(launch.FONT, fontNumber=0)
GLYPHS = TYPE.getGlyphSet()
CMAP = TYPE.getBestCmap()
UPEM = TYPE['head'].unitsPerEm
VARIANTS = {
    'on-dark': ('#6ee7b7', '#eff6f5'),
    'on-light': ('#08795d', '#080f1a'),
    'black': ('#000000', '#000000'),
    'white': ('#ffffff', '#ffffff'),
}
FORMS = {'symbol': (160, 160), 'horizontal': (1080, 160), 'stacked': (800, 340)}


def rgb(hex_color):
    return tuple(int(hex_color[i:i+2], 16) for i in (1, 3, 5))


def lettering(label, size, x, baseline, color):
    """Use real glyph outlines so SVGs don't depend on a recipient's fonts."""
    scale = size/UPEM
    paths = []
    for c in label:
        name = CMAP[ord(c)]
        pen = SVGPathPen(GLYPHS)
        GLYPHS[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        if pen.getCommands():
            paths.append(f'<path fill="{color}" d="{pen.getCommands()}"/>')
        x += TYPE['hmtx'][name][0]*scale-1.8
    return ''.join(paths)


def word_width(label, size):
    return sum(TYPE['hmtx'][CMAP[ord(c)]][0]*size/UPEM-1.8 for c in label)+1.8


def svg_mark(cx, cy, scale, color, ray):
    start = (80+47*math.cos(.25), 80+47*math.sin(.25))
    end = (80+47*math.cos(-.5), 80+47*math.sin(-.5))
    return (f'<g transform="translate({cx} {cy}) scale({scale}) translate(-80 -80)">'
            f'<path d="M {start[0]} {start[1]} A 47 47 0 1 1 {end[0]} {end[1]}" '
            f'fill="none" stroke="{color}" stroke-width="8"/>'
            f'<path d="M 94 76 L 137 55" fill="none" stroke="{ray}" stroke-width="7"/>'
            f'<circle cx="139" cy="54" r="6" fill="{color}"/></g>')


def layout(form):
    if form == 'stacked':
        return (400, 105, 1.25), (400-word_width('Open Recommender', 58)/2, 272, 58)
    if form == 'horizontal':
        return (80, 80, 1), (175, 104, 69)
    return (80, 80, 1), None


def export(form, variant):
    w, h = FORMS[form]
    color, ray = VARIANTS[variant]
    geometry, word = layout(form)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}" role="img" aria-label="Open Recommender">'
           '<title>Open Recommender</title>'+svg_mark(*geometry, color, ray))
    if word:
        svg += lettering('Open Recommender', word[2], word[0], word[1], ray)
    svg += '</svg>'
    name = f'open-recommender-{form}-{variant}'
    path = OUT/'svg'/f'{name}.svg'
    path.write_text(svg)
    ET.parse(path)
    # Share the exact film geometry, with supersampled transparent edges.
    im = Image.new('RGBA', (round(w*launch.S), round(h*launch.S)))
    launch.mark(im, *geometry, color=rgb(color), ray=rgb(ray))
    if word:
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype(launch.FONT, round(word[2]*launch.S), index=0)
        x = word[0]*launch.S
        for c in 'Open Recommender':
            d.text((x, word[1]*launch.S), c, font=f, fill=(*rgb(ray), 255), anchor='ls')
            x += f.getlength(c)-1.8*launch.S
    target = (1024, 1024) if form == 'symbol' else (w*3, h*3)
    im = im.resize(target, Image.Resampling.LANCZOS)
    im.save(OUT/'png'/f'{name}.png')
    assert im.getbbox() and im.getpixel((0, 0))[3] == 0


def icons():
    im = Image.new('RGBA', (1280, 1280))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, 1279, 1279), radius=260, fill=(*launch.INK, 255))
    launch.mark(im, 80, 80)
    im = im.resize((1024, 1024), Image.Resampling.LANCZOS)
    for size in (16, 32, 48, 64, 128, 256, 512, 1024):
        im.resize((size, size), Image.Resampling.LANCZOS).save(OUT/'icons'/f'icon-{size}.png')
    im.save(OUT/'icons'/'favicon.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    (OUT/'icons'/'app-icon.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 160">'
        '<title>Open Recommender app icon</title>'
        '<rect width="160" height="160" rx="32" fill="#080f1a"/>'+
        svg_mark(80, 80, 1, '#6ee7b7', '#eff6f5')+'</svg>')


def board():
    im = Image.new('RGB', (3200, 2080), launch.INK)
    d = ImageDraw.Draw(im)
    f = lambda size, bold=False: ImageFont.truetype(launch.FONT, size*2, index=0 if bold else 5)
    d.text((100, 82), 'Open Recommender', fill=launch.WHITE, font=f(39, True))
    d.text((102, 193), 'One mark. Every context.', fill=launch.MINT, font=f(19))
    boxes = ((80, 310, 'on-dark', launch.INK), (1640, 310, 'on-light', (248, 250, 246)),
             (80, 1150, 'white', (26, 34, 45)), (1640, 1150, 'black', (235, 240, 237)))
    for x, y, variant, bg in boxes:
        d.rounded_rectangle((x, y, x+1480, y+760), radius=42, fill=bg, outline=(55, 73, 83), width=2)
        fg = launch.WHITE if variant in ('on-dark', 'white') else launch.INK
        d.text((x+52, y+46), {'on-dark': 'COLOR / DARK', 'on-light': 'COLOR / LIGHT',
                             'white': 'ONE COLOR / WHITE', 'black': 'ONE COLOR / BLACK'}[variant],
               fill=fg, font=f(13))
        for form, box in (('horizontal', (x+75, y+190, 1320, 196)),
                          ('symbol', (x+110, y+460, 224, 224)),
                          ('stacked', (x+610, y+438, 660, 281))):
            tile = Image.open(OUT/'png'/f'open-recommender-{form}-{variant}.png')
            tile.thumbnail((box[2], box[3]), Image.Resampling.LANCZOS)
            im.paste(tile, (box[0], box[1]), tile)
    d.text((100, 1960), 'OUTLINED SVG  /  TRANSPARENT PNG  /  APP ICONS + FAVICON', fill=launch.MUTED, font=f(14))
    im.resize((1600, 1040), Image.Resampling.LANCZOS).save(OUT/'logo-board.jpg', quality=96)


def main():
    for folder in ('svg', 'png', 'icons'):
        (OUT/folder).mkdir(parents=True, exist_ok=True)
    for form in FORMS:
        for variant in VARIANTS:
            export(form, variant)
    icons()
    board()
    (OUT/'QUICKSTART.txt').write_text(
        'OPEN RECOMMENDER — LOGO KIT\n\n'
        'Three layouts: symbol, horizontal, stacked.\n'
        'Four treatments: on-dark, on-light, black, white.\n'
        'SVG: scalable vector artwork; all lettering is outlined. No font installation needed.\n'
        'PNG: transparent backgrounds; symbols 1024px, horizontal 3240px, stacked 2400px.\n'
        'Icons: 16–1024px, SVG app icon, and multi-size favicon.ico.\n\n'
        'Use on-dark over midnight or other dark backgrounds; on-light over white or pale backgrounds.\n'
        'Use black/white when only one ink color is available.\n'
        'Keep clear space around the artwork. Do not stretch, recolor, or rotate the mark.\n'
        'Primary palette: mint #6ee7b7, midnight #080f1a, off-white #eff6f5.\n'
        'Light-background mark: deep teal #08795d.\n'
        'Motion accents: coral #ff919e, gold #ffd07e, sky #6dbfff.\n')
    paths = sorted(p for p in OUT.rglob('*') if p.is_file() and p.suffix in ('.svg', '.png', '.ico', '.jpg', '.txt'))
    with zipfile.ZipFile(OUT/'open-recommender-logo-kit.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, 'open-recommender-logo-kit/'+str(path.relative_to(OUT)))
    assert len(list((OUT/'svg').glob('*.svg'))) == 12
    assert len(list((OUT/'png').glob('*.png'))) == 12
    print('Verified logo kit: 12 SVGs, 12 transparent PNGs, app icons, favicon, preview and usage guide.')


if __name__ == '__main__':
    main()
