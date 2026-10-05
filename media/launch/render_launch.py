"""Render the original 15-second Open Recommender launch film.

Run: python3 media/launch/render_launch.py
Requires Pillow, NumPy and ffmpeg. No downloaded artwork or music.
"""

from functools import lru_cache
from pathlib import Path
import json
import math
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
VIDEO_STEM = 'open-recommender-launch-v2'
OUTPUT_SIZE = (3840, 2160)
# Render every shape at twice the output resolution for antialiased edges.
W, H = (dimension * 2 for dimension in OUTPUT_SIZE)
FPS, DURATION = 60, 15
S = W / 960
BEAT = 60 / 128
CUTS = (0, 6 * BEAT, 12 * BEAT, 18 * BEAT, 24 * BEAT, 15)
MINT = (110, 231, 183)
WHITE = (239, 246, 245)
MUTED = (142, 165, 179)
BLUE = (110, 120, 255)
CORAL = (255, 145, 158)
GOLD = (255, 208, 126)
SKY = (109, 191, 255)
INK = (8, 15, 26)
FONT = '/System/Library/Fonts/Avenir Next.ttc'
MONO = '/System/Library/Fonts/Menlo.ttc'


def clamp(x):
    return max(0, min(1, x))


def ease(x):
    return 1 - (1 - clamp(x)) ** 4


def smooth(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def mix(a, b, t):
    return a + (b - a) * t


@lru_cache(maxsize=128)
def font(size, style='bold'):
    return ImageFont.truetype(MONO if style == 'mono' else FONT, round(size * S),
                              index=0 if style in ('bold', 'mono') else 5)


@lru_cache(maxsize=256)
def typeset(text, size, color=WHITE, style='bold', tracking=0):
    f = font(size, style)
    width = sum(f.getlength(c) for c in text) + tracking * S * max(0, len(text) - 1)
    img = Image.new('RGBA', (math.ceil(width) + 8, math.ceil(size * S * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 0
    top = f.getbbox('Hg')[1]
    for c in text:
        d.text((x, -top), c, font=f, fill=(*color, 255), anchor='la')
        x += f.getlength(c) + tracking * S
    return img


def text(im, label, x, y, size, color=WHITE, style='bold', alpha=1, tracking=0, scale=1, center=False):
    tile = typeset(label, size, color, style, tracking)
    if scale != 1:
        tile = tile.resize((max(1, round(tile.width * scale)), max(1, round(tile.height * scale))), Image.Resampling.BICUBIC)
    if alpha < 1:
        tile = tile.copy()
        tile.putalpha(tile.getchannel('A').point(lambda a: round(a * clamp(alpha))))
    im.alpha_composite(tile, (round(x * S - (tile.width / 2 if center else 0)), round(y * S)))


def line(d, points, fill, width=1):
    d.line([(round(x * S), round(y * S)) for x, y in points], fill=fill, width=max(1, round(width * S)), joint='curve')


def rounded(d, box, color, radius=16, outline=None, width=1):
    d.rounded_rectangle(tuple(round(v * S) for v in box), radius=round(radius * S), fill=color,
                        outline=outline, width=max(1, round(width * S)))


def dot(d, x, y, r, color):
    d.ellipse(((x-r)*S, (y-r)*S, (x+r)*S, (y+r)*S), fill=color)


def mark(im, cx, cy, scale=1, color=MINT, ray=WHITE, alpha=1):
    d = ImageDraw.Draw(im)
    pts = [(cx+47*scale*math.cos(a), cy+47*scale*math.sin(a))
           for a in np.linspace(.25, math.tau-.5, 240)]
    line(d, pts, (*color, round(255*alpha)), 8*scale)
    line(d, [(cx+14*scale, cy-4*scale), (cx+57*scale, cy-25*scale)],
         (*ray, round(255*alpha)), 7*scale)
    dot(d, cx+59*scale, cy-26*scale, 6*scale, (*color, round(255*alpha)))


def interest_icon(d, kind, x, y, size, color):
    if kind == 'Music':
        line(d, [(x-size*.3, y+size*.25), (x-size*.3, y-size*.35),
                 (x+size*.3, y-size*.5), (x+size*.3, y+size*.1)], color, 2)
        dot(d, x-size*.43, y+size*.3, size*.16, color)
        dot(d, x+size*.17, y+size*.15, size*.16, color)
    elif kind == 'Reading':
        for side in (-1, 1):
            line(d, [(x, y-size*.27), (x+side*size*.45, y-size*.4),
                     (x+side*size*.45, y+size*.32), (x, y+size*.43), (x, y-size*.27)], color, 1.7)
    else:
        d.polygon([(x-size*.25)*S, (y-size*.4)*S, (x+size*.4)*S, y*S,
                   (x-size*.25)*S, (y+size*.4)*S], fill=color)


def taste_card(im, x, y, scale=1):
    layer = Image.new('RGBA', im.size)
    d = ImageDraw.Draw(layer)
    rounded(d, (x-74*scale, y-56*scale, x+74*scale, y+56*scale), (19, 38, 47, 255),
            20*scale, (*MINT, 220), 1.3*scale)
    dot(d, x-44*scale, y-26*scale, 7*scale, MINT)
    d.arc(((x-56*scale)*S, (y-18*scale)*S, (x-32*scale)*S, (y+2*scale)*S), 180, 360,
          fill=MINT, width=max(1, round(3*scale*S)))
    for i, (kind, color) in enumerate((('Music', CORAL), ('Reading', GOLD), ('Video', SKY))):
        cx = x+(i-1)*43*scale
        rounded(d, (cx-17*scale, y+9*scale, cx+17*scale, y+43*scale), (*color, 35), 9*scale)
        interest_icon(d, kind, cx, y+26*scale, 17*scale, (*color, 255))
    im.alpha_composite(layer)
    text(im, 'YOUR TASTE', x-27*scale, y-28*scale, 12*scale, WHITE, 'bold')


@lru_cache(maxsize=1)
def background():
    yy, xx = np.mgrid[0:1080, 0:1920].astype(np.float32)
    glow1 = np.exp(-(((xx - 1460) / 680) ** 2 + ((yy - 420) / 530) ** 2))
    glow2 = np.exp(-(((xx - 850) / 640) ** 2 + ((yy - 1120) / 340) ** 2))
    rgb = np.zeros((1080, 1920, 3), dtype=np.float32) + INK
    rgb += glow1[..., None] * np.array([5, 26, 25])
    rgb += glow2[..., None] * np.array([18, 12, 34])
    noise = np.random.default_rng(42).normal(0, .65, (1080, 1920, 1))
    return Image.fromarray(np.clip(rgb + noise, 0, 255).astype(np.uint8)).resize(
        (W, H), Image.Resampling.LANCZOS).convert('RGBA')


@lru_cache(maxsize=1)
def orb():
    n = 2048
    yy, xx = np.mgrid[-1:1:complex(n), -1:1:complex(n)].astype(np.float32)
    rr = xx ** 2 + yy ** 2
    z = np.sqrt(np.maximum(0, 1 - rr))
    light = np.maximum(0, -.45 * xx - .6 * yy + .65 * z)
    fresnel = (1-z) ** 2
    spec = np.maximum(0, -.32 * xx - .52 * yy + .79*z) ** 40
    band = .5 + .5 * np.sin(9 * (xx*.55 + yy*.4 + z))
    rgb = np.zeros((n, n, 3), dtype=np.float32)
    for k, (base, lit, edge) in enumerate(zip((7, 24, 27), (42, 132, 111), (108, 129, 204))):
        rgb[..., k] = base + lit*light + edge*fresnel + 145*spec + 16*band*z
    alpha = np.clip((1-rr)*n/3, 0, 1)*255
    return Image.fromarray(np.dstack((np.clip(rgb, 0, 255), alpha)).astype(np.uint8))


def sphere(im, x, y, r, time, opacity=1):
    disc = orb().rotate(8 * math.sin(time*.4), resample=Image.Resampling.BICUBIC)
    disc = disc.resize((round(2*r*S), round(2*r*S)), Image.Resampling.LANCZOS)
    if opacity < 1:
        disc.putalpha(disc.getchannel('A').point(lambda a: round(a*opacity)))
    im.alpha_composite(disc, (round((x-r)*S), round((y-r)*S)))


def orbit_path(t, cx, cy, rx, ry, tilt=0):
    x, y = rx*math.cos(t), ry*math.sin(t)
    return cx + x*math.cos(tilt)-y*math.sin(tilt), cy + x*math.sin(tilt)+y*math.cos(tilt)


def orbit(d, cx, cy, rx, ry, t, color=MINT, tilt=-.4, alpha=.6):
    pts = [orbit_path(i*math.tau/180, cx, cy, rx, ry, tilt) for i in range(181)]
    line(d, pts, (*color, round(45*alpha)), .8)
    for j in range(3):
        a = t*1.7+j*math.tau/3
        trail = [orbit_path(a-i*.017, cx, cy, rx, ry, tilt) for i in range(42)]
        for i in range(len(trail)-1):
            line(d, trail[i:i+2], (*color, round((1-i/42)*180*alpha)), 2.3)
        x, y = trail[0]
        dot(d, x, y, 3, (*color, round(255*alpha)))


def chip(im, label, x, y, color=MINT, small=False):
    d = ImageDraw.Draw(im)
    size = 12 if small else 15
    tw = typeset(label, size).width/S
    rounded(d, (x, y, x+tw+45, y+36), (15, 30, 41, 245), 18, (*color, 130))
    dot(d, x+15, y+18, 3, color)
    text(im, label, x+25, y+10, size, WHITE, 'medium')


def headline(im, words, colors, u, x=58, y=157, size=90, gap=98):
    for i, word in enumerate(words):
        e = ease((u-i*.10)/.62)
        text(im, word, x, y+i*gap + (1-e)*95, size, colors[i], alpha=e, tracking=-3)


def chrome(im, time, label, number):
    d = ImageDraw.Draw(im)
    mark(im, 54, 36, .18)
    text(im, 'Open Recommender', 78, 29, 12, MUTED, 'medium')
    text(im, label, 40, 491, 10, MUTED, 'mono', tracking=.65)
    text(im, number, 890, 31, 11, MINT, 'mono')
    line(d, [(40, 518), (920, 518)], (48, 66, 75, 255), .7)
    line(d, [(40, 518), (40+880*time/DURATION, 518)], MINT, 1.3)


def scene(index, t, absolute):
    im = background().copy()
    fx = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(fx)
    # Slow star field; deterministic positions make the artwork reproducible.
    for i in range(64):
        x = ((i*137.508 + absolute*3) % 920)+20
        y = (i*i*19.37 % 450)+40
        dot(d, x, y, .65, (105, 160, 157, 44))
    if index == 0:
        cx, cy = 701, 272
        r = 124*mix(.25, 1, ease(t/.85))*(1+.02*math.sin(absolute*math.tau/BEAT))
        orbit(d, cx, cy, 210, 67, absolute, tilt=-.45)
        orbit(d, cx, cy, 164, 99, -absolute*.65, BLUE, .8, .7)
        im.alpha_composite(fx)
        sphere(im, cx, cy, r, absolute)
        text(im, 'YOU', cx, cy-17, 31, WHITE, center=True, alpha=ease((t-.3)/.5))
        front = Image.new('RGBA', im.size)
        df = ImageDraw.Draw(front)
        orbit(df, cx, cy, 210, 67, absolute, tilt=-.45, alpha=.7)
        im.alpha_composite(front)
        for i, (label, color) in enumerate(zip(('MUSIC', 'READING', 'VIDEO', 'IDEAS'), (CORAL, GOLD, SKY, MINT))):
            a = absolute*.65+i*math.tau/4+.5
            x, y = orbit_path(a, cx, cy, 174, 128, -.3)
            chip(im, label, x-42, y-18, color, True)
        text(im, 'DISCOVERY STARTS WITH YOU', 60, 112, 12, MUTED, 'medium', alpha=ease(t/.4), tracking=1.2)
        headline(im, ['YOUR', 'TASTE.'], [WHITE, MINT], t, size=101, gap=107)
        text(im, 'The music, stories and ideas you love.', 60, 405, 17, WHITE, 'medium', alpha=ease((t-.4)/.6))
        chrome(im, absolute, 'THE THINGS YOU LOVE, IN ONE PLACE', '01')
    elif index == 1:
        # A portable profile travels along three curved routes into independent sites.
        x0, y0 = 640, 272
        for j, (kind, color, end) in enumerate(zip(('Music', 'Reading', 'Video'), (CORAL, GOLD, SKY),
                                                  ((788, 144), (788, 274), (788, 404)))):
            pts = []
            for q in np.linspace(0, 1, 90):
                x = mix(x0, end[0], q)
                y = mix(y0, end[1], smooth(q))
                pts.append((x, y))
            line(d, pts, (*color, 100), 1.4)
            p = (t*.68-j*.18) % 1
            for k in range(12):
                q = max(0, p-k*.008)
                dot(d, mix(x0, end[0], q), mix(y0, end[1], smooth(q)), 3.5-k*.15, (*color, 255-k*17))
            rounded(d, (788, end[1]-52, 934, end[1]+52), (20, 31, 46, 255), 15, (*color, 150))
            for k in range(3):
                dot(d, 802+k*6, end[1]-40, 1.4, (*color, 180))
            line(d, [(799, end[1]-31), (922, end[1]-31)], (*color, 80), .7)
            rounded(d, (800, end[1]-20, 841, end[1]+23), (*color, 255), 9)
            interest_icon(d, kind, 820, end[1]+1, 22, (*INK, 255))
            for k in range(3):
                rounded(d, (852, end[1]-13+k*12, 922-k*10, end[1]-10+k*12), (*color, 180-k*30), 1)
        orbit(d, x0, y0, 102, 52, absolute, tilt=.6, alpha=.6)
        im.alpha_composite(fx)
        taste_card(im, x0, y0, .94+ease(t/.6)*.06)
        for kind, color, y in zip(('Music', 'Reading', 'Video'), (CORAL, GOLD, SKY), (144, 274, 404)):
            text(im, kind, 861, y+32, 11, color, 'medium', center=True)
        text(im, 'BRING WHAT YOU LOVE', 60, 112, 12, MUTED, 'medium', tracking=1.2)
        headline(im, ['TAKE YOUR', 'TASTE.'], [WHITE, MINT], t, size=74, gap=90)
        text(im, 'To the sites you choose.', 60, 370, 23, WHITE, 'medium', alpha=ease((t-.4)/.6))
        chrome(im, absolute, 'MUSIC. READING. VIDEO. CONNECTED BY YOU.', '02')
    elif index == 2:
        px = mix(1010, 586, ease(t/.6))
        py = 119+3*math.sin(t*1.7)
        orbit(d, 737, 270, 235, 124, absolute*.6, BLUE, -.45, .45)
        rounded(d, (px, py, px+302, py+298), (16, 28, 41, 255), 24, (70, 108, 111, 255), 1)
        line(d, [(px+23, py+60), (px+278, py+60)], (50, 75, 84, 255))
        im.alpha_composite(fx)
        text(im, 'Share with this site', px+24, py+25, 19, WHITE, 'bold')
        for j, (label, color) in enumerate(zip(('Music', 'Reading', 'Video'), (CORAL, GOLD, SKY))):
            y = py+91+j*65
            text(im, label, px+24, y+4, 22, color, 'medium')
            layer = Image.new('RGBA', im.size)
            dd = ImageDraw.Draw(layer)
            enabled = j < 2
            p = ease((t-.45-j*.23)/.38) if enabled else 0
            c = tuple(round(mix(a, b, p)) for a, b in zip((39, 53, 67), MINT))
            rounded(dd, (px+222, y, px+274, y+29), c, 15)
            dot(dd, px+237+p*22, y+14.5, 10.5, INK if p>.5 else MUTED)
            if j < 2:
                line(dd, [(px+24, y+47), (px+278, y+47)], (42, 59, 71, 255), .6)
            im.alpha_composite(layer)
        text(im, 'YOU SET THE LIMITS', 60, 112, 12, MUTED, 'medium', tracking=1.0)
        headline(im, ['YOU', 'DECIDE.'], [WHITE, MINT], t, size=96, gap=106)
        text(im, 'Choose what each site sees.', 60, 405, 21, WHITE, 'medium', alpha=ease((t-.4)/.6))
        chrome(im, absolute, 'SHARE THE INTERESTS YOU PICK', '03')
    elif index == 3:
        # The profile breaks out of a visual enclosure; a literal exit, no quality claims.
        center = (686, 270)
        escape = ease((t-.48)/1.05)
        for i in range(5):
            r = 90+i*24
            pts = [orbit_path(a, *center, r, r, -.35) for a in np.linspace(.2+escape*.4, math.tau-.2-escape*1.1, 180)]
            line(d, pts, (*BLUE, 150-i*22), 2-i*.22)
        path = [(mix(686, 940, q), 270-75*math.sin(q*math.pi*.8)) for q in np.linspace(0, escape, 70)]
        if len(path)>1:
            line(d, path, (*MINT, 160), 2)
        for k in range(9):
            q = max(0, escape-k*.018)
            dot(d, mix(686, 940, q), 270-75*math.sin(q*math.pi*.8), 5-k*.4, (*MINT, 140-k*12))
        im.alpha_composite(fx)
        x, y = mix(686, 884, escape), 270-65*math.sin(escape*math.pi*.8)
        taste_card(im, x, y, mix(.9, .65, escape))
        text(im, 'CHANGE SITES. KEEP YOUR TASTE.', 60, 112, 12, MUTED, 'medium', tracking=.6)
        headline(im, ['FREE', 'TO LEAVE.'], [WHITE, MINT], t, size=82, gap=96)
        text(im, 'Keep exploring, on your terms.', 60, 388, 20, WHITE, 'medium', alpha=ease((t-.4)/.6))
        chrome(im, absolute, 'TAKE YOUR TASTE WITH YOU', '04')
    else:
        # Signals resolve into an open circular mark and a quiet, readable end card.
        cx, cy = 480, 167
        for j in range(3):
            orbit(d, cx, cy, mix(370, 75+j*7, ease(t/.95)), mix(172, 32+j*7, ease(t/.95)),
                  absolute+j*.7, MINT if j != 1 else BLUE, -.4+j*.65, 1-clamp(t/1.65))
        im.alpha_composite(fx)
        layer = Image.new('RGBA', im.size)
        appear = ease((t-.38)/.7)
        mark(layer, cx, cy, alpha=appear)
        im.alpha_composite(layer)
        title_e = ease((t-.38)/.75)
        text(im, 'Open Recommender', 480, 250+(1-title_e)*35, 63, WHITE,
             alpha=title_e, tracking=-2.4, center=True)
        text(im, 'Your taste. Your terms.', 480, 342, 29, MINT, 'medium',
             alpha=ease((t-.85)/.6), center=True, tracking=-.5)
        layer = Image.new('RGBA', im.size)
        dd = ImageDraw.Draw(layer)
        badge_alpha = ease((t-1.15)/.5)
        rounded(dd, (382, 411, 578, 445), (16, 38, 38, round(255*badge_alpha)), 17,
                (83, 143, 126, round(200*badge_alpha)))
        im.alpha_composite(layer)
        text(im, 'EARLY PREVIEW', 480, 422, 11, MINT, 'medium', alpha=badge_alpha, center=True, tracking=1)
        text(im, 'Bring what you love. Choose what you share.', 480, 482, 14, MUTED, 'medium',
             alpha=badge_alpha, center=True)
    return im


def frame(t):
    index = next(i for i in range(5) if t < CUTS[i+1])
    u = t-CUTS[index]
    im = scene(index, u, t)
    # Fast directional wipe bridges the scenes, preserving continuously moving signals.
    if index > 0 and u < .32:
        previous = scene(index-1, CUTS[index]-CUTS[index-1], t)
        progress = smooth(u/.32)
        edge = round(W*(1-progress))
        mask = Image.new('L', (W, H), 0)
        dm = ImageDraw.Draw(mask)
        dm.polygon([(0, 0), (edge-37.5*S, 0), (edge+37.5*S, H), (0, H)], fill=255)
        im = Image.composite(previous, im, mask)
        fx = Image.new('RGBA', im.size)
        df = ImageDraw.Draw(fx)
        df.polygon([(edge-50*S, 0), (edge+2.5*S, 0), (edge+77.5*S, H), (edge+25*S, H)],
                   fill=(*MINT, round(220*math.sin(progress*math.pi))))
        im.alpha_composite(fx)
    fade = ease(t/.18)
    if fade < 1:
        im = Image.blend(Image.new('RGBA', im.size, (*INK, 255)), im, fade)
    return im.convert('RGB').resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)


def soundtrack():
    sr = 48000
    n = sr*DURATION
    audio = np.zeros((n, 2), np.float64)
    rng = np.random.default_rng(2026)

    def add(at, signal, gain=.2, pan=0):
        start = round(at*sr)
        if start >= n:
            return
        signal = np.asarray(signal)[:n-start]*gain
        audio[start:start+len(signal), 0] += signal*math.sqrt((1-pan)/2)
        audio[start:start+len(signal), 1] += signal*math.sqrt((1+pan)/2)

    def tone(freq, length, kind='pluck'):
        x = np.arange(round(length*sr))/sr
        if kind == 'bass':
            s = np.sin(math.tau*freq*x)+.25*np.sin(math.tau*freq*2*x)
            return s*np.minimum(1, x/.006)*np.exp(-x*5)
        s = np.sin(math.tau*freq*x)+.32*np.sin(math.tau*freq*2.002*x)+.12*np.sin(math.tau*freq*3*x)
        return s*np.minimum(1, x/.003)*np.exp(-x*7)

    # D minor / Bb / F / C: original 8-bar synth motif at 128 BPM.
    roots = [73.416, 73.416, 58.27, 58.27, 87.307, 87.307, 65.406, 73.416]
    for beat in range(32):
        at = beat*BEAT
        x = np.arange(round(sr*.31))/sr
        phase = math.tau*(45*x+115*.025*(1-np.exp(-x/.025)))
        kick = np.sin(phase)*np.exp(-x*18)+rng.normal(0, 1, len(x))*np.exp(-x*160)*.08
        add(at, kick, .54 if beat < 24 else .37)
        add(at, tone(roots[beat//4], .44, 'bass'), .24)
        if beat%2 == 1 and beat < 28:
            x = np.arange(round(sr*.19))/sr
            noise = rng.normal(0, 1, len(x))
            noise = noise-np.convolve(noise, np.ones(9)/9, mode='same')
            add(at, noise*np.exp(-x*28)+.25*np.sin(math.tau*180*x)*np.exp(-x*25), .12)
        for half in (0, .5):
            if beat > 28:
                continue
            x = np.arange(round(sr*.08))/sr
            noise = rng.normal(0, 1, len(x))
            hi = noise-np.convolve(noise, np.ones(11)/11, mode='same')
            add(at+half*BEAT, hi*np.exp(-x*65), .045, -.35 if half == 0 else .35)
        arp = [0, 7, 12, 15, 12, 7, 19, 15]
        for half in (0, .5):
            step = (beat*2+int(half*2))%8
            freq = roots[beat//4]*4*2**(arp[step]/12)
            sig = tone(freq, .52)
            add(at+half*BEAT, sig, .095, math.sin(beat*1.2)*.5)
            add(at+half*BEAT+BEAT*.75, sig, .027, -math.sin(beat*1.2)*.7)
    # Soft sustained harmony and designed transition sweeps.
    for bar, root in enumerate(roots):
        x = np.arange(round(4*BEAT*sr))/sr
        pad = sum(np.sin(math.tau*root*2*2**(semi/12)*x) for semi in (0, 3 if bar in (0, 1, 7) else 4, 7))/3
        env = np.sin(np.pi*np.arange(len(x))/len(x))**.7
        add(bar*4*BEAT, pad*env, .075, -.15)
    for cut in CUTS[1:-1]:
        x = np.arange(round(.65*sr))/sr
        noise = rng.normal(0, 1, len(x))
        airy = np.convolve(noise, np.ones(35)/35, mode='same')
        sweep = np.sin(math.tau*(220*x+1800*x*x))* .12
        add(cut-.42, (airy+sweep)*np.sin(np.pi*x/.65)**2, .20, .2)
    # The end resolves into a D-minor chord with a long stereo tail.
    for semi in (0, 3, 7, 12):
        add(CUTS[4], tone(293.665*2**(semi/12), 3.7), .10, (semi-6)/16)
    audio *= np.minimum(1, np.arange(n)/(.025*sr))[:, None]
    audio *= np.minimum(1, (n-1-np.arange(n))/(.40*sr))[:, None]
    audio = np.tanh(audio*1.3)
    audio *= .89/max(.01, np.max(np.abs(audio)))
    path = OUT/'soundtrack.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes((audio*32767).astype('<i2').tobytes())
    return path


def verify(path, size=OUTPUT_SIZE):
    sample = Image.new('RGBA', (384, 384))
    line(ImageDraw.Draw(sample), [(3, 3), (42, 29)], (*MINT, 255), 2)
    alpha = np.asarray(sample.resize((192, 192), Image.Resampling.LANCZOS).getchannel('A'))
    assert np.any((alpha > 0) & (alpha < 255)), 'Edges must retain antialiased coverage'
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height']) == size
    assert video['r_frame_rate'] == '60/1'
    assert int(video['nb_frames']) == FPS*DURATION
    assert abs(float(info['format']['duration'])-DURATION) < .025
    assert audio['channels'] == 2
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], check=True)
    print(f'Verified: 15.000 seconds / {size[0]} × {size[1]} / 60 fps / stereo audio.', flush=True)


def main():
    if '--verify' in sys.argv:
        verify(OUT/f'{VIDEO_STEM}-4k.mp4')
        verify(OUT/f'{VIDEO_STEM}.mp4', (1920, 1080))
        return
    if '--stills' in sys.argv:
        samples = (1.8, 4.5, 7.4, 10.0, 13.6)
        sheet = Image.new('RGB', (960, 810), INK)
        for i, t in enumerate(samples):
            shot = frame(t)
            shot.save(OUT/f'frame-{i+1}.jpg', quality=94)
            shot.thumbnail((480, 270))
            sheet.paste(shot, ((i%2)*480, (i//2)*270))
        sheet.save(OUT/'contact-sheet.jpg', quality=94)
        frame(13.6).save(OUT/'poster.jpg', quality=96)
        return
    wav = soundtrack()
    output = OUT/f'{VIDEO_STEM}-4k.mp4'
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}', '-r', str(FPS), '-i', '-', '-i', str(wav), '-map', '0:v', '-map', '1:a',
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '16', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '256k', '-t', str(DURATION), '-movflags', '+faststart',
           '-metadata', 'title=Open Recommender — Your taste. Your terms.',
           '-metadata', 'comment=Original motion design and synthesized soundtrack.', str(output)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(FPS*DURATION):
            proc.stdin.write(frame(i/FPS).tobytes())
            if i%60 == 0:
                print(f'Rendered {i//60}/{DURATION} seconds', flush=True)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError('Video encoder failed')
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    frame(13.6).save(OUT/'poster.jpg', quality=96)
    verify(output)
    companion = OUT/f'{VIDEO_STEM}.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(output),
                    '-vf', 'scale=1920:1080:flags=lanczos', '-c:v', 'libx264', '-preset', 'fast',
                    '-crf', '16', '-c:a', 'copy', '-movflags', '+faststart', str(companion)], check=True)
    verify(companion, (1920, 1080))


if __name__ == '__main__':
    main()
