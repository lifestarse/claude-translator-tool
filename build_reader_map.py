import html
import io
import json
import re
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
for _need in ("skeleton.json", "merged.json"):
    if not os.path.exists(os.path.join(ROOT, "glossmap", _need)):
        raise SystemExit("Нет glossmap/%s — карта мира собирается из скелета "
                         "co-occurrence и сведённых выводов агентов glossmap "
                         "(см. README, стадия 10)." % _need)
sk = json.load(io.open(os.path.join(ROOT, "glossmap", "skeleton.json"), encoding="utf-8"))
merged = json.load(io.open(os.path.join(ROOT, "glossmap", "merged.json"), encoding="utf-8"))

ORDER = ["Персонажи и титулы", "Юниты Линь", "Существа и виды",
         "Цивилизации и фракции", "Локации", "Оружие и устройства",
         "Явления и силы", "Прочее"]

def slug(ru, used):
    s = "e-" + re.sub(r"[^a-zа-яё0-9]+", "-", ru.lower()).strip("-")
    base, n = s, 2
    while s in used:
        s = "%s-%d" % (base, n)
        n += 1
    used.add(s)
    return s

used = set()
entries = []
for cn, v in sk.items():
    m = merged.get(cn, {})
    entries.append({
        "cn": cn, "ru": v["ru"], "grp": v["grp"], "gen": v["gen"],
        "desc": m.get("desc") or v["desc"],
        "rels": m.get("relations", []), "arc": m.get("arc", ""),
        "first": v["first"], "last": v["last"], "count": v["count"],
    })
entries.sort(key=lambda e: (-e["count"], e["ru"]))
id_of = {}
for e in entries:
    id_of[e["ru"]] = slug(e["ru"], used)

groups = {g: [] for g in ORDER}
for e in entries:
    groups.setdefault(e["grp"], []).append(e)
total = len(entries)
n_rel = sum(len(e["rels"]) for e in entries)

head = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Карта новеллы — {{BOOK_TITLE}}</title>
<style>
:root{
  --bg:#F7F5F0; --panel:#FFFFFF; --ink:#1E2422; --muted:#68716C;
  --accent:#2E7D66; --accent-soft:#E3EEE9; --amber:#8A6D3B; --line:#E0DCD2;
  --chip:#EFECE4; --hl:#F4E9CE;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#14181A; --panel:#1C2224; --ink:#E4E1D8; --muted:#9AA49E;
  --accent:#5FBFA0; --accent-soft:#20302B; --amber:#C9A96A; --line:#2A3234;
  --chip:#242C2E; --hl:#3A3322;}}
:root[data-theme="dark"]{
  --bg:#14181A; --panel:#1C2224; --ink:#E4E1D8; --muted:#9AA49E;
  --accent:#5FBFA0; --accent-soft:#20302B; --amber:#C9A96A; --line:#2A3234;
  --chip:#242C2E; --hl:#3A3322;
}
:root[data-theme="light"]{
  --bg:#F7F5F0; --panel:#FFFFFF; --ink:#1E2422; --muted:#68716C;
  --accent:#2E7D66; --accent-soft:#E3EEE9; --amber:#8A6D3B; --line:#E0DCD2;
  --chip:#EFECE4; --hl:#F4E9CE;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px 80px}
header.hero{padding:44px 0 10px}
.hero h1{font-family:Georgia,"Times New Roman",serif;font-weight:400;
  font-size:clamp(28px,4.5vw,42px);margin:0 0 6px;text-wrap:balance;letter-spacing:.2px}
.hero .sub{color:var(--muted);margin:0;font-size:15px;max-width:72ch}
.hero .sub b{color:var(--accent);font-weight:600}
.bar{position:sticky;top:0;background:var(--bg);padding:14px 0 12px;z-index:5;
  border-bottom:1px solid var(--line)}
.bar-inner{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
#q{flex:1 1 260px;min-width:200px;padding:10px 14px;font:inherit;
  color:var(--ink);background:var(--panel);border:1px solid var(--line);
  border-radius:8px;outline:none}
#q:focus{border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12.5px;padding:5px 11px;border-radius:999px;cursor:pointer;
  background:var(--chip);border:1px solid var(--line);color:var(--muted);
  user-select:none;white-space:nowrap}
.chip .n{opacity:.65;font-variant-numeric:tabular-nums}
.chip.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:600}
.chip:focus-visible,#q:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
#stat{color:var(--muted);font-size:12.5px;margin-left:auto;font-variant-numeric:tabular-nums}
section{margin-top:34px}
section>h2{font-family:Georgia,"Times New Roman",serif;font-weight:400;font-size:22px;
  margin:0 0 4px;letter-spacing:.2px}
section>.gc{color:var(--muted);font-size:12px;text-transform:uppercase;
  letter-spacing:.08em;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,320px),1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:13px 15px 12px;display:flex;flex-direction:column;gap:6px;scroll-margin-top:90px}
.card:target{border-color:var(--accent);background:var(--accent-soft)}
.card .t{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.card .ru{font-weight:650;font-size:15.5px}
.card .gen{font-size:10.5px;color:var(--accent);background:var(--accent-soft);
  border-radius:4px;padding:1px 6px;letter-spacing:.05em}
.card .cn{color:var(--amber);font-size:12.5px;
  font-family:"Microsoft YaHei","PingFang SC",sans-serif}
.card .arc{color:var(--accent);font-size:12px;font-style:italic}
.card .d{color:var(--muted);font-size:13.5px;line-height:1.5;margin:0}
.card .meta{color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums;
  letter-spacing:.03em}
.rels{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}
.rel{font-size:11.5px;text-decoration:none;color:var(--accent);
  background:var(--accent-soft);border-radius:5px;padding:2px 8px}
.rel b{font-weight:600}
.rel span{opacity:.75}
.rel:hover{text-decoration:underline}
.none{display:none}
#empty{display:none;color:var(--muted);padding:60px 0;text-align:center;
  font-family:Georgia,serif;font-size:17px}
@media (prefers-reduced-motion: no-preference){
  .card{transition:border-color .15s,background .3s}
  .card:hover{border-color:var(--accent)}
  html{scroll-behavior:smooth}
}
@media (max-width: 640px){
  .wrap{padding:0 12px 60px}
  header.hero{padding:24px 0 6px}
  .hero .sub{font-size:14px}
  .bar{padding:10px 0 8px}
  .bar-inner{gap:8px}
  #q{flex-basis:100%;font-size:16px}
  .chips{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;
    max-width:100%;padding-bottom:4px;scrollbar-width:thin}
  .chip{flex:0 0 auto}
  #stat{margin-left:0;flex-basis:100%;text-align:right;margin-top:-2px}
  section{margin-top:26px}
  .card{padding:12px 13px 11px;scroll-margin-top:120px}
  .card .d{font-size:13px}
}
</style>"""

body = ['<div class="wrap">',
        '<header class="hero"><h1>Карта новеллы</h1>',
        '<p class="sub">«{{BOOK_TITLE}}» — <b>%d</b> имён и названий, <b>%d</b> связей между ними. У каждой записи: место в сюжете, главы появления и связи — по ним можно ходить как по карте. Поиск понимает русское имя, 中文-термин и слова описания.</p>' % (total, n_rel),
        '</header>',
        '<div class="bar"><div class="bar-inner">',
        '<input id="q" type="search" placeholder="Найти: имя, 中文 или слово из описания…" aria-label="Поиск по карте">',
        '<div class="chips" id="chips">']
SLUGG = {g: "g%d" % i for i, g in enumerate(ORDER)}
for g in ORDER:
    n = len(groups.get(g, []))
    if n:
        body.append('<button class="chip" data-g="%s">%s <span class="n">%d</span></button>'
                    % (SLUGG[g], html.escape(g), n))
body.append('</div><span id="stat"></span></div></div>')

for g in ORDER:
    items = groups.get(g, [])
    if not items:
        continue
    body.append('<section id="%s"><h2>%s</h2><p class="gc">%d записей · по убыванию присутствия в книге</p><div class="grid">'
                % (SLUGG[g], html.escape(g), len(items)))
    for e in items:
        search = " ".join([e["ru"], e["cn"], e["desc"], e["arc"]] +
                          [r["name"] for r in e["rels"]]).lower().replace("ё", "е")
        bits = ['<article class="card" id="%s" data-s="%s">' % (
                id_of[e["ru"]], html.escape(search, quote=True)),
                '<div class="t"><span class="ru">%s</span>' % html.escape(e["ru"])]
        if e["gen"]:
            bits.append('<span class="gen">%s</span>' % e["gen"])
        if e["cn"]:
            bits.append('<span class="cn">%s</span>' % html.escape(e["cn"]))
        bits.append('</div>')
        if e["arc"]:
            bits.append('<div class="arc">%s</div>' % html.escape(e["arc"]))
        bits.append('<p class="d">%s</p>' % html.escape(e["desc"]))
        if e["rels"]:
            rel_bits = ['<div class="rels">']
            for r in e["rels"]:
                tgt = id_of.get(r["name"])
                if not tgt:
                    continue
                rel_bits.append('<a class="rel" href="#%s"><b>%s</b>%s</a>' % (
                    tgt, html.escape(r["name"]),
                    ' <span>· %s</span>' % html.escape(r["rel"]) if r["rel"] else ''))
            rel_bits.append('</div>')
            bits.append("".join(rel_bits))
        if e["first"]:
            span = ("гл. %d–%d" % (e["first"], e["last"])) if e["last"] != e["first"] else ("гл. %d" % e["first"])
            bits.append('<div class="meta">%s · встречается в %d гл.</div>' % (span, e["count"]))
        bits.append('</article>')
        body.append("".join(bits))
    body.append('</div></section>')

body.append('<p id="empty">Ничего не нашлось — попробуй короче или без падежа.</p></div>')

script = """<script>
(function(){
  var q=document.getElementById('q'),chips=[].slice.call(document.querySelectorAll('.chip')),
      cards=[].slice.call(document.querySelectorAll('.card')),
      secs=[].slice.call(document.querySelectorAll('section')),
      stat=document.getElementById('stat'),empty=document.getElementById('empty'),
      active={};
  chips.forEach(function(c){c.addEventListener('click',function(){
    var g=c.dataset.g;
    if(active[g]){delete active[g];c.classList.remove('on');}
    else{active[g]=1;c.classList.add('on');}
    apply();
  });});
  var t;
  q.addEventListener('input',function(){clearTimeout(t);t=setTimeout(apply,120);});
  document.addEventListener('click',function(ev){
    var a=ev.target.closest('a.rel');
    if(!a)return;
    var card=document.getElementById(a.getAttribute('href').slice(1));
    if(card&&card.classList.contains('none')){
      q.value='';active={};chips.forEach(function(c){c.classList.remove('on');});
      apply();
    }
  });
  function apply(){
    var needle=q.value.trim().toLowerCase().replace(/ё/g,'е'),
        anyChip=Object.keys(active).length>0,shown=0;
    secs.forEach(function(s){
      var vis=0,on=!anyChip||active[s.id];
      [].slice.call(s.querySelectorAll('.card')).forEach(function(card){
        var ok=on&&(!needle||card.dataset.s.indexOf(needle)>-1);
        card.classList.toggle('none',!ok);
        if(ok)vis++;
      });
      s.classList.toggle('none',vis===0);
      shown+=vis;
    });
    stat.textContent=shown+' из '+cards.length;
    empty.style.display=shown?'none':'block';
  }
  apply();
})();
</script>"""

page = head + "\n" + "\n".join(body) + "\n" + script
out = os.path.join(ROOT, "reader_glossary.html")
io.open(out, "w", encoding="utf-8", newline="\n").write(page)
print("записано:", out, "|", len(page), "байт |", total, "карточек |", n_rel, "связей")
