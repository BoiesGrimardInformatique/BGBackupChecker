"""Génération du tableau de bord HTML local (fichier statique, aucun accès
réseau depuis la page, auto-actualisation par meta refresh). Les filtres,
heures relatives et panneaux de détail sont en JavaScript embarqué et
fonctionnent en file://, sans aucune ressource externe."""

import html
import json
import os
import tempfile
from datetime import datetime, timedelta

from . import (
    BackupEvent,
    load_timezone,
    JobState,
    SEVERITY,
    STATUS_ERROR,
    STATUS_MISSING,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
    STATUS_WARNING,
)
from . import history as history_mod

# Icône + libellé : la couleur ne porte jamais l'information seule.
BADGES = {
    STATUS_ERROR: ("✕", "Erreur", "critical"),
    STATUS_WARNING: ("⚠", "Avertissement", "warning"),
    STATUS_SUCCESS: ("✓", "Succès", "good"),
    STATUS_MISSING: ("⏱", "Manquant", "serious"),
    STATUS_UNKNOWN: ("?", "Inconnu", "muted"),
}

PRODUCT_LABELS = {
    "macrium": "Macrium", "retrospect": "Retrospect",
    "sqlagent": "SQL Server Agent", "pbs": "Proxmox Backup Server",
    "script": "Script personnalisé",
}
NO_CLIENT = "(non assigné)"
# Nombre max de lignes de la section Historique (les tâches en difficulté
# remontent en premier, la coupe ne cache donc que des tâches saines).
MAX_HISTORY_ROWS = 60

_CSS = """
:root { color-scheme: light dark; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page); color: var(--ink); padding: 24px;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --ring: rgba(11,11,11,0.10);
  --wash: rgba(11,11,11,0.045);
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  body {
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --ring: rgba(255,255,255,0.10);
    --wash: rgba(255,255,255,0.06);
  }
}
main { max-width: 1160px; margin: 0 auto; }
h1 { font-size: 1.25rem; font-weight: 650; }
h2 { font-size: 0.95rem; font-weight: 600; margin: 28px 0 10px; }
.meta { color: var(--ink-2); font-size: 0.82rem; margin-top: 4px; }
.chip { display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem;
        border: 1px solid var(--ring); border-radius: 999px; padding: 2px 10px;
        color: var(--ink-2); background: var(--surface); }
.chip.stale { border-color: var(--serious); color: var(--ink); }
.chip.dead { border-color: var(--critical); color: var(--ink); }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 12px; margin-top: 18px; }
.tile { background: var(--surface); border: 1px solid var(--ring);
        border-radius: 10px; padding: 14px 16px; text-align: left;
        font: inherit; color: inherit; cursor: pointer; }
.tile:hover { background: var(--wash); }
.tile:focus-visible { outline: 2px solid var(--ink-2); outline-offset: 2px; }
.tile .num { font-size: 1.9rem; font-weight: 650; line-height: 1.2; }
.tile .lbl { font-size: 0.82rem; color: var(--ink-2); margin-top: 2px;
             display: flex; align-items: center; gap: 6px; }
.tile.alert { border-color: var(--critical); border-width: 2px; }
.tile[aria-pressed="true"] { outline: 2px solid var(--ink-2); outline-offset: -2px; }

.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
           margin: 0 0 10px; }
.seg { display: inline-flex; border: 1px solid var(--ring); border-radius: 8px;
       overflow: hidden; background: var(--surface); }
.seg button { font: inherit; font-size: 0.8rem; color: var(--ink-2);
              background: none; border: none; padding: 5px 11px; cursor: pointer; }
.seg button + button { border-left: 1px solid var(--grid); }
.seg button:hover { background: var(--wash); }
.seg button[aria-pressed="true"] { background: var(--wash); color: var(--ink);
                                   font-weight: 600; }
.filters input[type="search"], .filters select {
  font: inherit; font-size: 0.8rem; color: var(--ink);
  background: var(--surface); border: 1px solid var(--ring); border-radius: 8px;
  padding: 5px 10px; }
.filters input[type="search"] { min-width: 220px; }
.filters .count { font-size: 0.78rem; color: var(--muted); margin-left: auto; }

.wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--ring);
        border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th { text-align: left; font-weight: 600; color: var(--ink-2); padding: 9px 12px;
     border-bottom: 1px solid var(--grid); white-space: nowrap; }
td { padding: 8px 12px; border-bottom: 1px solid var(--grid); vertical-align: top; }
tr:last-child td { border-bottom: none; }
td.dt { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--ink-2); }
tbody tr.row:hover { background: var(--wash); }
tr.accent-critical td:first-child { box-shadow: inset 3px 0 0 var(--critical); }
tr.accent-serious td:first-child { box-shadow: inset 3px 0 0 var(--serious); }

.badge { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.badge .ic { font-weight: 700; }
.c-good .ic { color: var(--good); } .c-warning .ic { color: var(--warning); }
.c-serious .ic { color: var(--serious); } .c-critical .ic { color: var(--critical); }
.c-muted .ic { color: var(--muted); }

button.expander { font: inherit; font-size: 0.8rem; line-height: 1;
  background: none; border: 1px solid var(--ring); border-radius: 6px;
  color: var(--ink-2); width: 24px; height: 24px; cursor: pointer; }
button.expander:hover { background: var(--wash); }
button.expander[aria-expanded="true"] { transform: rotate(90deg); }
tr.detail td { background: var(--wash); font-size: 0.82rem; }
tr.detail dl { display: grid; grid-template-columns: max-content 1fr;
               gap: 4px 14px; }
tr.detail dt { color: var(--muted); }
tr.detail dd { color: var(--ink-2); overflow-wrap: anywhere; }
tr.detail code { font-size: 0.78rem; background: var(--surface);
                 border: 1px solid var(--ring); border-radius: 4px;
                 padding: 0 4px; }
#clients-resume tbody tr { cursor: pointer; }
#clients-resume td.n { font-variant-numeric: tabular-nums; text-align: right; }
#clients-resume th.n { text-align: right; }
#historique td.hd, #historique th.hd { text-align: center; padding: 6px 3px;
  font-variant-numeric: tabular-nums; }
#historique td.hd .ic { font-weight: 700; }
#historique td.n, #historique th.n { font-variant-numeric: tabular-nums;
  text-align: right; }
.note { color: var(--muted); font-size: 0.78rem; margin-top: 24px; }
.empty { padding: 14px; color: var(--muted); font-size: 0.85rem; }
.banner { border: 1px solid var(--serious); border-left-width: 4px;
          background: var(--surface); border-radius: 8px;
          padding: 10px 14px; margin-top: 16px; font-size: 0.85rem; }
.banner ul { margin: 6px 0 0 18px; color: var(--ink-2); }
@media print { .filters, .chip, button.expander { display: none; }
               tr.detail { display: none; } body { padding: 0; } }
"""

_JS = """
(function () {
  'use strict';
  var MIN = 60000;

  // --- Heures relatives ------------------------------------------------
  function rel(ts) {
    var d = Date.now() - ts;
    if (d < MIN) return "à l'instant";
    var m = Math.round(d / MIN);
    if (m < 60) return 'il y a ' + m + ' min';
    var h = Math.floor(m / 60);
    if (h < 48) return 'il y a ' + h + ' h' + (m % 60 ? ' ' + (m % 60) : '');
    return 'il y a ' + Math.round(h / 24) + ' j';
  }
  function tickRel() {
    document.querySelectorAll('[data-ts]').forEach(function (el) {
      el.querySelector('.rel').textContent = rel(+el.dataset.ts);
    });
  }

  // --- Fraîcheur du fichier généré --------------------------------------
  function tickFresh() {
    var el = document.getElementById('fresh');
    var age = Date.now() - GEN_TS;
    el.querySelector('.rel').textContent = rel(GEN_TS);
    el.classList.remove('stale', 'dead');
    if (age > REFRESH_MS * 6) {
      el.classList.add('dead');
      el.querySelector('.ic').textContent = '✕';
      el.title = 'Le fichier ne se régénère plus — vérifier la tâche planifiée.';
    } else if (age > REFRESH_MS * 2.5) {
      el.classList.add('stale');
      el.querySelector('.ic').textContent = '⚠';
      el.title = 'Données possiblement périmées.';
    }
  }

  // --- Filtres ----------------------------------------------------------
  var state = { produit: 'tous', etat: 'tous', client: 'tous', q: '' };
  try {
    var saved = JSON.parse(localStorage.getItem('bm-filtres') || '{}');
    if (saved.produit) state.produit = saved.produit;
    if (saved.etat) state.etat = saved.etat;
    if (saved.client) state.client = saved.client;
    if (typeof saved.q === 'string') state.q = saved.q;
  } catch (e) {}

  function apply() {
    try { localStorage.setItem('bm-filtres', JSON.stringify(state)); }
    catch (e) {}
    var shown = 0;
    document.querySelectorAll('#mails tbody tr.row').forEach(function (tr) {
      var ok = (state.produit === 'tous' || tr.dataset.produit === state.produit)
        && (state.etat === 'tous' || tr.dataset.etat === state.etat)
        && (state.client === 'tous' || tr.dataset.client === state.client)
        && (!state.q || tr.dataset.texte.indexOf(state.q) !== -1);
      tr.hidden = !ok;
      var det = tr.nextElementSibling;
      if (det && det.classList.contains('detail') && !ok) {
        det.hidden = true;
        tr.querySelector('.expander').setAttribute('aria-expanded', 'false');
      }
      if (ok) shown++;
    });
    document.getElementById('nb-affiches').textContent =
      shown + ' courriel' + (shown > 1 ? 's' : '') + ' affiché' + (shown > 1 ? 's' : '');
    document.querySelectorAll('.seg button').forEach(function (b) {
      b.setAttribute('aria-pressed',
        String(state[b.dataset.cle] === b.dataset.valeur));
    });
    document.querySelectorAll('.tile[data-etat]').forEach(function (t) {
      t.setAttribute('aria-pressed', String(state.etat === t.dataset.etat));
    });
  }

  document.querySelectorAll('.seg button').forEach(function (b) {
    b.addEventListener('click', function () {
      state[b.dataset.cle] = b.dataset.valeur;
      apply();
    });
  });
  var champ = document.getElementById('recherche');
  champ.value = state.q;
  champ.addEventListener('input', function () {
    state.q = champ.value.trim().toLowerCase();
    apply();
  });
  var selClient = document.getElementById('filtre-client');
  if (selClient) {
    selClient.value = state.client;
    if (selClient.value !== state.client) { state.client = 'tous'; }
    selClient.addEventListener('change', function () {
      state.client = selClient.value;
      apply();
    });
  } else if (state.client !== 'tous') {
    // Filtre client mémorisé mais plus aucun sélecteur (section clients
    // retirée) : sans ce reset, toutes les lignes resteraient masquées
    // sans aucun contrôle visible pour s'en sortir.
    state.client = 'tous';
  }

  // Vue par client : cliquer une ligne filtre les courriels sur ce client.
  document.querySelectorAll('#clients-resume tbody tr').forEach(function (tr) {
    tr.addEventListener('click', function () {
      state.client = (state.client === tr.dataset.client)
        ? 'tous' : tr.dataset.client;
      if (selClient) selClient.value = state.client;
      apply();
      document.getElementById('mails').scrollIntoView({ behavior: 'smooth' });
    });
  });

  // Tuiles KPI : filtrent le tableau des courriels ; « Manquants » mène aux tâches.
  document.querySelectorAll('.tile[data-etat]').forEach(function (t) {
    t.addEventListener('click', function () {
      state.etat = (state.etat === t.dataset.etat) ? 'tous' : t.dataset.etat;
      apply();
      document.getElementById('mails').scrollIntoView({ behavior: 'smooth' });
    });
  });
  var tuileManquants = document.querySelector('.tile[data-cible]');
  if (tuileManquants) {
    tuileManquants.addEventListener('click', function () {
      document.getElementById('taches').scrollIntoView({ behavior: 'smooth' });
    });
  }

  // --- Détail dépliable ---------------------------------------------------
  document.querySelectorAll('.expander').forEach(function (b) {
    b.addEventListener('click', function () {
      var det = b.closest('tr').nextElementSibling;
      var open = det.hidden;
      det.hidden = !open;
      b.setAttribute('aria-expanded', String(open));
    });
  });

  tickRel(); tickFresh(); apply();
  setInterval(function () { tickRel(); tickFresh(); }, MIN);
})();
"""


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def _badge(status: str) -> str:
    icon, label, cls = BADGES.get(status, BADGES[STATUS_UNKNOWN])
    return (f'<span class="badge c-{cls}"><span class="ic">{icon}</span>'
            f'{label}</span>')


def _dt_cell(dt) -> str:
    if not dt:
        return '<td class="dt">—</td>'
    ts = int(dt.timestamp() * 1000)
    return (f'<td class="dt" data-ts="{ts}" '
            f'title="{dt.strftime("%Y-%m-%d %H:%M")}">'
            f'{dt.strftime("%Y-%m-%d %H:%M")}<br>'
            f'<span class="rel"></span></td>')


def _tiles(counts: dict, basis: str) -> str:
    # « Inconnus » visible au même niveau que les autres états : si Macrium
    # change ses libellés après une mise à jour, des dizaines de courriels
    # passent « inconnus » — ça doit se voir sans dérouler le tableau.
    order = [
        (STATUS_ERROR, "Erreurs"), (STATUS_WARNING, "Avertissements"),
        (STATUS_MISSING, "Manquants"), (STATUS_UNKNOWN, "Inconnus"),
        (STATUS_SUCCESS, "Succès"),
    ]
    tiles = []
    for status, label in order:
        n = counts.get(status, 0)
        icon, _, cls = BADGES[status]
        alert = " alert" if status == STATUS_ERROR and n > 0 else ""
        if status == STATUS_MISSING:
            attr = 'data-cible="taches" title="Voir les tâches attendues"'
        else:
            attr = (f'data-etat="{status}" aria-pressed="false" '
                    'title="Filtrer les courriels sur cet état"')
        tiles.append(
            f'<button class="tile{alert}" {attr}><div class="num">{n}</div>'
            f'<div class="lbl c-{cls}"><span class="ic">{icon}</span>'
            f'{label}</div></button>'
        )
    return (f'<div class="tiles">{"".join(tiles)}</div>'
            f'<p class="meta">{_esc(basis)}</p>')


def _jobs_table(states: list[JobState]) -> str:
    if not states:
        return ('<div class="wrap"><p class="empty">Aucune tâche attendue '
                "configurée (section expected_jobs de config.yaml) — seuls les "
                "courriels reçus sont analysés.</p></div>")
    rows = []
    for s in states:
        last = s.last_event
        detail = s.due_note or (last.subject if last else "")
        accent = ""
        if s.status == STATUS_ERROR:
            accent = ' class="accent-critical"'
        elif s.status == STATUS_MISSING:
            accent = ' class="accent-serious"'
        rows.append(
            f"<tr{accent}>"
            f"<td>{_esc(s.name)}</td>"
            f"<td>{_esc(s.client or NO_CLIENT)}</td>"
            f"<td>{_esc(PRODUCT_LABELS.get(s.product, s.product))}</td>"
            f"<td>{_badge(s.status)}</td>"
            f"{_dt_cell(last.received if last else None)}"
            f"<td>{_esc(detail)}</td>"
            "</tr>"
        )
    return ('<div class="wrap"><table><thead><tr><th>Tâche</th><th>Client</th>'
            "<th>Produit</th><th>État</th><th>Dernier courriel</th>"
            "<th>Détail</th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _history_section(cfg: dict, history: dict | None, now: datetime) -> str:
    """Bande des derniers jours par tâche suivie (pire état de chaque jour),
    jours en échec et taux de réussite sur 30 jours. Les tâches en difficulté
    remontent en premier — c'est la section « quoi surveiller ce matin »."""
    taches = (history or {}).get("taches") or {}
    if not taches:
        return ""
    conf = cfg.get("history") or {}
    show = max(1, int(conf.get("show_days", 14)))
    days = [now - timedelta(days=i) for i in range(show - 1, -1, -1)]
    keys = [d.strftime("%Y-%m-%d") for d in days]

    entries = []
    for t in taches.values():
        jours = t.get("jours") or {}
        bad = sum(1 for k in keys
                  if jours.get(k) in (STATUS_ERROR, STATUS_MISSING))
        ok, total = history_mod.success_rate(jours, now)
        entries.append((bad, t.get("client") or "", t.get("nom") or "?",
                        jours, ok, total))
    entries.sort(key=lambda e: (-e[0], e[1] == "", e[1], e[2]))

    day_heads = "".join(
        f'<th class="hd" title="{d.strftime("%Y-%m-%d")}">{d.day}</th>'
        for d in days)
    rows = []
    for bad, client, nom, jours, ok, total in entries[:MAX_HISTORY_ROWS]:
        cells = []
        for k in keys:
            status = jours.get(k)
            if status is None:
                cells.append(f'<td class="hd c-muted" '
                             f'title="{k} : aucune donnée">'
                             '<span class="ic">·</span></td>')
            else:
                icon, label, cls = BADGES.get(status, BADGES[STATUS_UNKNOWN])
                cells.append(f'<td class="hd c-{cls}" title="{k} : {label}">'
                             f'<span class="ic">{icon}</span></td>')
        rate = (f'<span title="{ok} jour(s) en succès sur {total} '
                f'renseigné(s)">{round(100 * ok / total)} %</span>'
                if total else "—")
        accent = ' class="accent-critical"' if bad else ""
        rows.append(
            f"<tr{accent}><td>{_esc(nom)}</td>"
            f"<td>{_esc(client or NO_CLIENT)}</td>"
            f'{"".join(cells)}'
            f'<td class="n">{bad or "—"}</td>'
            f'<td class="n">{rate}</td></tr>')
    trunc = ""
    if len(entries) > MAX_HISTORY_ROWS:
        trunc = (f'<p class="meta">{len(entries) - MAX_HISTORY_ROWS} tâche(s) '
                 "sans difficulté récente non affichée(s).</p>")
    return (
        "<h2>Historique — pire état par jour</h2>"
        '<div class="wrap" id="historique"><table><thead><tr>'
        f'<th>Tâche</th><th>Client</th>{day_heads}'
        f'<th class="n" title="Jours en erreur ou manquant sur les '
        f'{show} affichés">Échecs</th>'
        '<th class="n">Taux 30 j</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        f'<p class="meta">Pire état de chaque jour ({show} derniers jours, '
        "mémorisés dans historique.json à chaque exécution). Les tâches en "
        f"difficulté sont en tête.</p>{trunc}")


def _detail_row(ev: BackupEvent) -> str:
    pattern = (f"<code>{_esc(ev.matched_pattern)}</code>"
               if ev.matched_pattern else
               "aucun motif reconnu — à calibrer dans config.yaml (parsers)")
    att = ""
    if ev.attachments_note:
        att = f"<dt>Pièces jointes</dt><dd>{_esc(ev.attachments_note)}</dd>"
    return (
        '<tr class="detail" hidden><td colspan="8"><dl>'
        f"<dt>Expéditeur</dt><dd>{_esc(ev.sender) or '—'}</dd>"
        f"<dt>Dossier</dt><dd>{_esc(ev.folder)}</dd>"
        f"<dt>Motif déclencheur</dt><dd>{pattern}</dd>"
        f"{att}"
        f"<dt>Extrait</dt><dd>{_esc(ev.excerpt) or '(corps vide)'}</dd>"
        "</dl></td></tr>"
    )


def _events_table(events: list[BackupEvent], max_rows: int) -> str:
    if not events:
        return ('<div class="wrap" id="mails"><p class="empty">Aucun courriel '
                "trouvé dans la fenêtre d'analyse.</p></div>")
    rows = []
    for ev in events[:max_rows]:
        haystack = " ".join(
            [ev.subject, ev.machine, ev.job, ev.sender, ev.folder,
             ev.client]).lower()
        accent = ' accent-critical' if ev.status == STATUS_ERROR else ""
        rows.append(
            f'<tr class="row{accent}" data-produit="{_esc(ev.product)}" '
            f'data-etat="{_esc(ev.status)}" '
            f'data-client="{_esc(ev.client or NO_CLIENT)}" '
            f'data-texte="{_esc(haystack)}">'
            '<td><button class="expander" aria-expanded="false" '
            'title="Afficher le détail">▸</button></td>'
            f"{_dt_cell(ev.received)}"
            f"<td>{_esc(ev.client or '—')}</td>"
            f"<td>{_esc(PRODUCT_LABELS.get(ev.product, ev.product))}</td>"
            f"<td>{_badge(ev.status)}</td>"
            f"<td>{_esc(ev.machine or '—')}</td>"
            f"<td>{_esc(ev.job or '—')}</td>"
            f"<td>{_esc(ev.subject)}</td>"
            "</tr>"
            f"{_detail_row(ev)}"
        )
    trunc = ""
    if len(events) > max_rows:
        trunc = (f'<p class="meta">{len(events) - max_rows} courriels plus '
                 "anciens non affichés (voir report.max_rows).</p>")
    return ('<div class="wrap" id="mails"><table><thead><tr>'
            '<th aria-label="Détail"></th><th>Reçu</th><th>Client</th>'
            "<th>Produit</th><th>État</th><th>Machine</th><th>Tâche</th>"
            "<th>Sujet</th></tr>"
            f'</thead><tbody>{"".join(rows)}</tbody></table></div>{trunc}')


def _client_summary(states: list[JobState], events: list[BackupEvent],
                    now: datetime) -> str:
    """Vue par client : pire état + comptes, basée sur les tâches attendues
    si elles existent, sinon sur les courriels des dernières 24 h."""
    groups: dict[str, dict] = {}

    def bump(client: str, status: str):
        g = groups.setdefault(client or NO_CLIENT,
                              {st: 0 for st in SEVERITY})
        g[status] = g.get(status, 0) + 1

    if states:
        for s in states:
            bump(s.client, s.status)
    else:
        for ev in events:
            if (now - ev.received).total_seconds() <= 24 * 3600:
                bump(ev.client, ev.status)
    if not groups or list(groups) == [NO_CLIENT]:
        return ""  # aucun client défini : la section n'apporterait rien

    def worst(g: dict) -> str:
        for st in SEVERITY:
            if g.get(st):
                return st
        return STATUS_UNKNOWN

    ordered = sorted(groups.items(),
                     key=lambda kv: (SEVERITY.index(worst(kv[1])), kv[0]))
    rows = []
    for client, g in ordered:
        w = worst(g)
        accent = ""
        if w == STATUS_ERROR:
            accent = ' class="accent-critical"'
        elif w == STATUS_MISSING:
            accent = ' class="accent-serious"'
        rows.append(
            f'<tr{accent} data-client="{_esc(client)}" tabindex="0" '
            'title="Filtrer les courriels sur ce client">'
            f"<td>{_esc(client)}</td><td>{_badge(w)}</td>"
            f'<td class="n">{g.get(STATUS_ERROR, 0)}</td>'
            f'<td class="n">{g.get(STATUS_MISSING, 0)}</td>'
            f'<td class="n">{g.get(STATUS_WARNING, 0)}</td>'
            f'<td class="n">{g.get(STATUS_SUCCESS, 0)}</td>'
            "</tr>")
    basis = ("tâches attendues" if states else "courriels des dernières 24 h")
    return (
        "<h2>Vue par client</h2>"
        '<div class="wrap" id="clients-resume"><table><thead><tr>'
        "<th>Client</th><th>Pire état</th>"
        '<th class="n">Erreurs</th><th class="n">Manquants</th>'
        '<th class="n">Avert.</th><th class="n">Succès</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        f'<p class="meta">Basée sur les {basis}. '
        "Cliquer un client pour filtrer les courriels.</p>")


def _error_banner(errors: list[str] | None) -> str:
    if not errors:
        return ""
    items = "".join(f"<li>{_esc(e)}</li>" for e in errors)
    return (
        '<div class="banner" role="alert">'
        '<span class="badge c-serious"><span class="ic">⚠</span>'
        "Collecte partielle</span> — certains dossiers n'ont pas pu être "
        f"lus, les comptes ci-dessous sont incomplets :<ul>{items}</ul></div>")


def _filters(clients: list[str]) -> str:
    def seg(cle: str, options: list[tuple[str, str]]) -> str:
        btns = "".join(
            f'<button data-cle="{cle}" data-valeur="{val}" '
            f'aria-pressed="false">{label}</button>'
            for val, label in options)
        return f'<div class="seg" role="group">{btns}</div>'

    produits = [("tous", "Tous"), ("macrium", "Macrium"),
                ("retrospect", "Retrospect"), ("sqlagent", "SQL Agent"),
                ("pbs", "Proxmox"), ("script", "Script")]
    etats = [("tous", "Tous"), (STATUS_ERROR, "Erreur"),
             (STATUS_WARNING, "Avert."), (STATUS_SUCCESS, "Succès"),
             (STATUS_UNKNOWN, "Inconnu")]
    sel = ""
    if clients:
        opts = '<option value="tous">Tous les clients</option>' + "".join(
            f'<option value="{_esc(c)}">{_esc(c)}</option>' for c in clients)
        sel = (f'<select id="filtre-client" '
               f'aria-label="Filtrer par client">{opts}</select>')
    return (
        '<div class="filters">'
        + seg("produit", produits) + seg("etat", etats) + sel
        + '<input type="search" id="recherche" '
          'placeholder="Client, machine, tâche, sujet…" '
          'aria-label="Recherche dans les courriels">'
        + '<span class="count" id="nb-affiches"></span>'
        + "</div>"
    )


def render(cfg: dict, events: list[BackupEvent], states: list[JobState],
           fetch_errors: list[str] | None = None,
           history: dict | None = None) -> str:
    tz = load_timezone(cfg["analysis"]["timezone"])
    now = datetime.now(tz)
    refresh = int(cfg["report"]["refresh_seconds"])
    days = cfg["analysis"]["days_back"]

    if states:
        counts = {}
        for s in states:
            counts[s.status] = counts.get(s.status, 0) + 1
        basis = ("Comptes basés sur l'état courant des tâches attendues. "
                 "Cliquer une tuile pour filtrer les courriels.")
    else:
        counts = {}
        for ev in events:
            if (now - ev.received).total_seconds() <= 24 * 3600:
                counts[ev.status] = counts.get(ev.status, 0) + 1
        basis = ("Comptes basés sur les courriels des dernières 24 h "
                 "(aucune tâche attendue configurée). "
                 "Cliquer une tuile pour filtrer les courriels.")

    refresh_label = (f"{refresh // 60} min" if refresh % 60 == 0
                     else f"{refresh} s")
    clients: list[str] = []
    for src in ([s.client for s in states] + [e.client for e in events]):
        name = src or NO_CLIENT
        if name not in clients:
            clients.append(name)
    clients.sort(key=lambda c: (c == NO_CLIENT, c))
    if clients == [NO_CLIENT]:
        clients = []  # aucun client configuré : pas de filtre inutile
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sauvegardes — Macrium &amp; Retrospect</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>État des sauvegardes — Macrium &amp; Retrospect</h1>
<p class="meta">
<span class="chip" id="fresh"><span class="ic">●</span>
généré {now.strftime("%Y-%m-%d %H:%M")} · <span class="rel"></span></span>
&nbsp;fenêtre d'analyse : {days} jours · page actualisée toutes les
{refresh_label} · {len(events)} courriels analysés</p>
{_error_banner(fetch_errors)}
{_tiles(counts, basis)}
{_client_summary(states, events, now)}
<h2 id="taches">État par tâche attendue</h2>
{_jobs_table(states)}
{_history_section(cfg, history, now)}
<h2>Courriels analysés</h2>
{_filters(clients)}
{_events_table(events, int(cfg["report"]["max_rows"]))}
<p class="note">Lecture seule : aucun courriel n'est modifié, déplacé ni marqué
comme lu dans Outlook/Exchange. Fichier local — aucune donnée n'est publiée.</p>
</main>
<script>
var GEN_TS = {json.dumps(int(now.timestamp() * 1000))};
var REFRESH_MS = {json.dumps(refresh * 1000)};
{_JS}
</script>
</body>
</html>"""


def write(cfg: dict, html_text: str) -> str:
    out = cfg["report"]["output"]
    if not os.path.isabs(out):
        out = os.path.join(cfg["_dir"], out)
    # Écriture atomique : le navigateur ne voit jamais un fichier partiel.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html_text)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return out
