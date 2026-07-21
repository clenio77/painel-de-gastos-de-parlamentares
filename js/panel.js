/**
 * CVP-IA Audit Panel — client logic
 * Security: esc/safeUrl on all API-sourced DOM output
 * Fluency: dedupe, debounce, lazy audit, single filter binds
 */
(function () {
  'use strict';

  const SUPA_URL = 'https://kwuxwwmbmfllpzpdlmih.supabase.co';
  const SUPA_KEY =
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt3dXh3d21ibWZsbHB6cGRsbWloIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc0MDE1NDYsImV4cCI6MjA5Mjk3NzU0Nn0.OhsWUbLV0S0o-cW7LoRiPpueuHxi9qLS6iFN7AquP_U';

  const ITEMS_PER_PAGE = 50;
  const RANKING_PER_PAGE = 30;

  let supabase = null;
  let DATA = {
    parlamentares: [],
    allScores: [],
    despesas: [],
    allDespesas: [],
    votos: [],
    allVotos: [],
    proposicoes: [],
    fornecedoresEnrich: {},
    candidaturas_tse: [],
    top_fornecedores: [],
    alertas_auditoria: [],
    total_gasto: 0,
    loadedAt: null,
    periods: [],
  };

  let currentPeriod = 'all';
  let currentPageRanking = 1;
  let currentPageGastos = 1;
  let currentPageVotacoes = 1;
  let currentPageProp = 1;
  let filtersBound = false;
  let auditComputed = false;
  let activeTab = 'ranking';
  let searchQuery = '';

  /* ---------- Security helpers ---------- */
  function esc(v) {
    if (v == null) return '';
    return String(v)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function safeUrl(url) {
    if (!url || typeof url !== 'string') return null;
    try {
      const u = new URL(url.trim());
      if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
      return u.href;
    } catch {
      return null;
    }
  }

  function docLink(url, label) {
    const href = safeUrl(url);
    if (!href) {
      return '<span class="muted" title="Sem comprovante digital">S/N</span>';
    }
    return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer" class="doc-link">${esc(label || 'Nota')}</a>`;
  }

  /* ---------- Formatters ---------- */
  function fmt(v) {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v || 0);
  }
  function sc(s) {
    return s >= 70 ? 'sh' : s >= 40 ? 'sm' : 'sl';
  }
  function scC(s) {
    return s >= 70 ? '#10b981' : s >= 40 ? '#f59e0b' : '#ef4444';
  }
  function valClass(v) {
    return v >= 5000 ? 'hi' : v >= 1000 ? 'md' : 'lo';
  }
  function debounce(fn, ms) {
    let t;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  function parsePeriod(p) {
    if (!p) return 0;
    const m = String(p).match(/(\d{1,2})[\/\-](\d{4})/);
    if (m) return parseInt(m[2], 10) * 100 + parseInt(m[1], 10);
    const iso = String(p).match(/(\d{4})-(\d{2})/);
    if (iso) return parseInt(iso[1], 10) * 100 + parseInt(iso[2], 10);
    return 0;
  }

  function periodLabel(p) {
    return p || '—';
  }

  function inPeriod(dateStr) {
    if (currentPeriod === 'all' || !dateStr) return currentPeriod === 'all';
    const d = String(dateStr);
    const [mm, yyyy] = currentPeriod.split('/');
    if (!mm || !yyyy) return true;
    return d.startsWith(`${yyyy}-${mm.padStart(2, '0')}`) || d.includes(`${yyyy}-${mm}`);
  }

  function scoreInPeriod(s) {
    if (currentPeriod === 'all') return true;
    return String(s.periodo || '') === currentPeriod || String(s.periodo || '').replace('-', '/') === currentPeriod;
  }

  /* ---------- Tabs ---------- */
  function showTab(id) {
    activeTab = id;
    document.querySelectorAll('.tab[data-tab]').forEach((t) => {
      const on = t.getAttribute('data-tab') === id;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.panel').forEach((p) => {
      const on = p.id === 'panel-' + id;
      p.classList.toggle('active', on);
      p.hidden = !on;
    });
    if (id === 'auditoria' && !auditComputed) {
      computeAuditoriaLazy().then(() => renderAuditoria());
    }
    if (id === 'metodologia') renderMetodologia();
  }

  /* ---------- Data shaping ---------- */
  function dedupeScoresByParlamentar(scores) {
    const byId = new Map();
    for (const s of scores) {
      if (!s.parlamentar_id && !(s.parlamentares && s.parlamentares.id)) continue;
      const pid = s.parlamentar_id || s.parlamentares.id;
      const prev = byId.get(pid);
      if (!prev || parsePeriod(s.periodo) > parsePeriod(prev.periodo)) {
        byId.set(pid, s);
      }
    }
    return Array.from(byId.values());
  }

  function buildParlamentaresFromScores(scores) {
    const latest = currentPeriod === 'all' ? dedupeScoresByParlamentar(scores) : scores.filter(scoreInPeriod);
    const list = (currentPeriod === 'all' ? latest : dedupeScoresByParlamentar(latest)).map((s) => {
      const p = Object.assign({}, s.parlamentares || {});
      p.id = s.parlamentar_id || p.id;
      p.score_geral = s.score_geral;
      p.score_atividade = s.score_atividade;
      p.score_gasto = s.score_gasto;
      p.score_transparencia = s.score_transparencia;
      p.score_coerencia = s.score_coerencia;
      p.dados_insuficientes = !!(s.dados_insuficientes || s.detalhes?.dados_insuficientes);
      p.periodo = s.periodo;
      p.detalhes = s.detalhes || {};
      return p;
    });
    return list.filter((p) => p.id != null && p.score_geral != null);
  }

  function filterDespesasByPeriod(despesas) {
    if (currentPeriod === 'all') return despesas;
    return despesas.filter((d) => inPeriod(d.data_documento) || d.periodo === currentPeriod);
  }

  function filterVotosByPeriod(votos) {
    if (currentPeriod === 'all') return votos;
    return votos.filter((v) => inPeriod(v.data));
  }

  function applyGlobalSearch(parlamentares) {
    if (!searchQuery) return parlamentares;
    const q = searchQuery.toLowerCase();
    return parlamentares.filter(
      (p) =>
        (p.nome_parlamentar || '').toLowerCase().includes(q) ||
        (p.partido || '').toLowerCase().includes(q) ||
        (p.uf || '').toLowerCase().includes(q)
    );
  }

  /* ---------- Auditoria (lazy) ---------- */
  async function computeAuditoriaLazy() {
    const despesas = DATA.despesas || [];
    const allAlerts = [];
    const gastosDiarios = {};
    const chunk = 400;
    for (let i = 0; i < despesas.length; i++) {
      const d = despesas[i];
      d.alertas = d.alertas || [];
      d.suspeita = !!d.suspeita;
      const tipoUpper = (d.tipo_despesa || '').toUpperCase();

      if (d.valor >= 5000 && d.valor % 1000 === 0) {
        const alerta = {
          tipo: 'Valor Redondo',
          gravidade: 'media',
          descricao: 'Valor exato e alto pode indicar nota fria ou superfaturamento.',
        };
        d.alertas.push(alerta);
        allAlerts.push(Object.assign({}, d, alerta));
        d.suspeita = true;
      }
      if (
        (tipoUpper.includes('ALIMENTAÇÃO') || tipoUpper.includes('REFEIÇÃO') || tipoUpper.includes('ALIMENTACAO')) &&
        d.valor > 200
      ) {
        const alerta = {
          tipo: 'Refeição Excessiva',
          gravidade: 'alta',
          descricao: 'Refeição acima do limite razoável individual.',
        };
        d.alertas.push(alerta);
        allAlerts.push(Object.assign({}, d, alerta));
        d.suspeita = true;
      }
      if (d.data_documento) {
        const day = new Date(d.data_documento).getUTCDay();
        if (
          (day === 0 || day === 6) &&
          !tipoUpper.includes('HOSPEDAGEM') &&
          !tipoUpper.includes('PASSAGEM') &&
          !tipoUpper.includes('COMBUST') &&
          !tipoUpper.includes('LOCA')
        ) {
          const alerta = {
            tipo: 'Gasto em Fim de Semana',
            gravidade: 'media',
            descricao: `Despesa em ${day === 6 ? 'sábado' : 'domingo'} sem justificativa clara de deslocamento.`,
          };
          d.alertas.push(alerta);
          allAlerts.push(Object.assign({}, d, alerta));
          d.suspeita = true;
        }
      }
      const enrich = DATA.fornecedoresEnrich[d.cnpj_fornecedor];
      if (enrich && (enrich.situacao === 'IRREGULAR' || enrich.sancionado_tcu)) {
        const alerta = {
          tipo: enrich.sancionado_tcu ? 'Fornecedor Sancionado TCU' : 'CNPJ Irregular',
          gravidade: 'alta',
          descricao: enrich.detalhe || 'Fornecedor com restrição cadastral ou sanção.',
        };
        d.alertas.push(alerta);
        allAlerts.push(Object.assign({}, d, alerta));
        d.suspeita = true;
      }
      if (d.data_documento && d.cnpj_fornecedor) {
        const key = `${d.parlamentar_id}_${d.data_documento}_${d.cnpj_fornecedor}`;
        if (!gastosDiarios[key]) gastosDiarios[key] = [];
        gastosDiarios[key].push(d);
      }
      if (i % chunk === 0) await new Promise((r) => setTimeout(r, 0));
    }
    for (const key of Object.keys(gastosDiarios)) {
      const notas = gastosDiarios[key];
      if (notas.length >= 3) {
        notas.forEach((n) => {
          const alerta = {
            tipo: 'Fracionamento',
            gravidade: 'alta',
            descricao: `${notas.length} notas no mesmo dia para o mesmo fornecedor.`,
          };
          n.alertas.push(alerta);
          allAlerts.push(Object.assign({}, n, alerta));
          n.suspeita = true;
        });
      }
    }
    DATA.alertas_auditoria = allAlerts;
    auditComputed = true;
  }

  function buildTopFornecedores(despesas) {
    const fMap = {};
    despesas.forEach((d) => {
      if (!d.fornecedor) return;
      const k = d.fornecedor;
      if (!fMap[k]) {
        fMap[k] = {
          fornecedor: k,
          cnpj_fornecedor: d.cnpj_fornecedor,
          total: 0,
          qtd: 0,
          parls: new Set(),
        };
      }
      fMap[k].total += d.valor || 0;
      fMap[k].qtd += 1;
      fMap[k].parls.add(d.nome_parlamentar);
    });
    return Object.values(fMap)
      .sort((a, b) => b.total - a.total)
      .slice(0, 80)
      .map((x) =>
        Object.assign({}, x, {
          parlamentares: Array.from(x.parls).join(', '),
          enrich: DATA.fornecedoresEnrich[x.cnpj_fornecedor] || null,
        })
      );
  }

  /* ---------- Render: stats / ranking ---------- */
  function renderStats() {
    const r = applyGlobalSearch(DATA.parlamentares || []);
    const d = DATA.despesas || [];
    const tg = d.reduce((s, x) => s + (x.valor || 0), 0);
    const scored = r.filter((x) => !x.dados_insuficientes);
    const avg = scored.reduce((s, x) => s + (x.score_geral || 0), 0) / Math.max(scored.length, 1);
    document.getElementById('stats').innerHTML = `
      <div class="stat"><b style="color:var(--blue)">${r.length}</b><small>Parlamentares</small></div>
      <div class="stat"><b style="color:var(--yellow)">${esc(fmt(tg))}</b><small>Total Gastos</small></div>
      <div class="stat"><b style="color:var(--purple)">${d.length}</b><small>Despesas</small></div>
      <div class="stat"><b style="color:var(--cyan)">${avg.toFixed(1)}</b><small>Score Médio</small></div>`;
  }

  function renderPeriodSelect() {
    const sel = document.getElementById('filt-periodo');
    if (!sel) return;
    const cur = currentPeriod;
    sel.innerHTML =
      '<option value="all">Acumulado (todos)</option>' +
      (DATA.periods || []).map((p) => `<option value="${esc(p)}"${p === cur ? ' selected' : ''}>${esc(periodLabel(p))}</option>`).join('');
  }

  function renderRanking(resetPage) {
    if (resetPage !== false) currentPageRanking = 1;
    let r = applyGlobalSearch(DATA.parlamentares || [])
      .filter((p) => p.score_geral != null)
      .sort((a, b) => (b.score_geral || 0) - (a.score_geral || 0));

    const depCount = r.filter((x) => x.casa === 'camara').length;
    const senCount = r.filter((x) => x.casa === 'senado').length;
    document.getElementById('total-tag').textContent = `${depCount} dep. • ${senCount} sen.`;

    const despByParl = {};
    (DATA.despesas || []).forEach((d) => {
      if (!despByParl[d.parlamentar_id]) despByParl[d.parlamentar_id] = [];
      despByParl[d.parlamentar_id].push(d);
    });

    const start = (currentPageRanking - 1) * RANKING_PER_PAGE;
    const pageData = r.slice(start, start + RANKING_PER_PAGE);

    document.getElementById('ranking').innerHTML = pageData
      .map((p, i) => {
        const pos = start + i;
        const s = p.score_geral || 0;
        const sa = p.score_atividade || 0;
        const sg = p.score_gasto || 0;
        const st = p.score_transparencia;
        const scohr = p.score_coerencia;
        const casa =
          p.casa === 'camara' ? '<span class="tg tg-c">Câmara</span>' : '<span class="tg tg-s">Senado</span>';
        const desps = despByParl[p.id] || [];
        const totalG = desps.reduce((acc, d) => acc + (d.valor || 0), 0);
        const insuf = p.dados_insuficientes
          ? '<span class="tg tg-warn" title="Poucos votos/despesas no período">Dados insuficientes</span>'
          : '';
        const transpHtml =
          st != null
            ? `<div class="sp" title="Transparência TSE"><b class="${sc(st)}">${Number(st).toFixed(0)}</b><small>Transp.</small></div>`
            : '';
        const cohHtml =
          scohr != null
            ? `<div class="sp" title="Coerência partidária"><b class="${sc(scohr)}">${Number(scohr).toFixed(0)}</b><small>Coerência</small></div>`
            : '';

        return `<div class="rk-row">
      <div class="pk" role="button" tabindex="0" data-toggle-det="${pos}" data-open-ficha="${esc(p.id)}">
        <div class="pk__r">${pos === 0 ? '1' : pos === 1 ? '2' : pos === 2 ? '3' : pos + 1}</div>
        <div>
          <div class="pk__n">${esc(p.nome_parlamentar)} ${insuf}</div>
          <div class="pk__m">${casa}<span>${esc(p.partido || '')} • ${esc(p.uf || '')}</span>
            <span>${esc(fmt(totalG))} (${desps.length} notas)</span>
            <span class="period-chip">${esc(periodLabel(p.periodo) || currentPeriod)}</span>
          </div>
        </div>
        <div class="pk__sc">
          <div class="sp" title="Presença em votações"><b class="${sc(sa)}">${Number(sa).toFixed(0)}</b><small>Presença</small></div>
          <div class="sp" title="Economia da cota"><b class="${sc(sg)}">${Number(sg).toFixed(0)}</b><small>Economia</small></div>
          ${transpHtml}${cohHtml}
          <div class="sp" title="Nota CVP-IA"><b class="${sc(s)}">${Number(s).toFixed(1)}</b><small>Nota</small>
            <div class="bar"><div style="width:${Math.min(100, s)}%;background:${scC(s)}"></div></div>
          </div>
        </div>
      </div>
      <div class="detail" id="det-${pos}"></div>
    </div>`;
      })
      .join('');

    bindRankingClicks(pageData, despByParl, start);
    renderPag('pag-ranking', r.length, currentPageRanking, 'ranking', RANKING_PER_PAGE);
  }

  function bindRankingClicks(pageData, despByParl, start) {
    document.querySelectorAll('.pk[data-toggle-det]').forEach((el) => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('a')) return;
        const pos = Number(el.getAttribute('data-toggle-det'));
        const idx = pos - start;
        const p = pageData[idx];
        if (ev.detail === 2 || ev.shiftKey) {
          openFicha(p.id);
          return;
        }
        toggleDet(pos, p, despByParl[p.id] || []);
      });
      el.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') openFicha(el.getAttribute('data-open-ficha'));
      });
    });
    document.querySelectorAll('.pk .pk__n').forEach((el) => {
      el.style.cursor = 'pointer';
      el.title = 'Clique para expandir despesas; Shift+clique para ficha completa';
    });
  }

  function toggleDet(pos, p, desps) {
    const el = document.getElementById('det-' + pos);
    if (!el) return;
    if (el.classList.contains('open')) {
      el.classList.remove('open');
      return;
    }
    const totalG = desps.reduce((s, d) => s + (d.valor || 0), 0);
    const det = p.detalhes || {};
    const breakdown = `<div class="score-break">
      <span>Presença: <b class="${sc(p.score_atividade || 0)}">${Number(p.score_atividade || 0).toFixed(0)}</b></span>
      <span>Economia: <b class="${sc(p.score_gasto || 0)}">${Number(p.score_gasto || 0).toFixed(0)}</b></span>
      <span>Transp.: <b class="${sc(p.score_transparencia || 0)}">${Number(p.score_transparencia || 0).toFixed(0)}</b></span>
      <span>Coerência: <b class="${sc(p.score_coerencia || 0)}">${Number(p.score_coerencia || 0).toFixed(0)}</b></span>
      ${det.n_votos != null ? `<span>Votos no período: ${esc(det.n_votos)}</span>` : ''}
    </div>
    <button type="button" class="pag-btn" data-ficha="${esc(p.id)}">Abrir ficha completa</button>`;

    if (!desps.length) {
      el.innerHTML = `${breakdown}<p class="empty-msg">Nenhuma despesa neste período.</p>`;
    } else {
      const rows = desps
        .slice()
        .sort((a, b) => b.valor - a.valor)
        .slice(0, 50)
        .map((d) => {
          const tipo = esc((d.tipo_despesa || '').substring(0, 40));
          return `<div class="exp-row"${d.suspeita ? ' style="background:rgba(239,68,68,.1)"' : ''}>
            <div class="tipo" title="${esc(d.tipo_despesa || '')}">${tipo}${d.suspeita ? ' !' : ''}</div>
            <div class="forn">${esc(d.fornecedor || 'N/A')}${
            d.cnpj_fornecedor
              ? `<br><small class="mono">${esc(d.cnpj_fornecedor)}</small>`
              : ''
          }</div>
            <div class="data">${esc((d.data_documento || '').split('T')[0] || 'S/ Data')}<br>${docLink(d.url_documento, 'Nota')}</div>
            <div class="val">${esc(fmt(d.valor))}</div>
          </div>`;
        })
        .join('');
      el.innerHTML = `${breakdown}
        <h3 class="detail-title">${desps.length} notas — Total: ${esc(fmt(totalG))}</h3>
        <div class="exp-grid">
          <div class="exp-row exp-head"><div>Tipo</div><div>Fornecedor</div><div>Data</div><div style="text-align:right">Valor</div></div>
          ${rows}
        </div>`;
    }
    el.querySelectorAll('[data-ficha]').forEach((btn) =>
      btn.addEventListener('click', () => openFicha(btn.getAttribute('data-ficha')))
    );
    el.classList.add('open');
  }

  /* ---------- Gastos ---------- */
  function renderGastos() {
    const desps = DATA.despesas || [];
    const parls = [...new Set(desps.map((d) => d.nome_parlamentar).filter(Boolean))].sort();
    const partidos = [...new Set(desps.map((d) => d.partido).filter(Boolean))].sort();
    const tipos = [...new Set(desps.map((d) => d.tipo_despesa).filter(Boolean))].sort();
    const fp = document.getElementById('filt-parl');
    const fpartido = document.getElementById('filt-partido');
    const ft = document.getElementById('filt-tipo');
    if (fp)
      fp.innerHTML =
        '<option value="">Todos os parlamentares</option>' +
        parls.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
    if (fpartido)
      fpartido.innerHTML =
        '<option value="">Todos os partidos</option>' +
        partidos.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
    if (ft)
      ft.innerHTML =
        '<option value="">Todos os tipos</option>' +
        tipos.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
    currentPageGastos = 1;
    filterGastos(false);
  }

  function filterGastos(resetPage) {
    if (resetPage !== false) currentPageGastos = 1;
    const fp = document.getElementById('filt-parl')?.value || '';
    const fpartido = document.getElementById('filt-partido')?.value || '';
    const ft = document.getElementById('filt-tipo')?.value || '';
    const ff = (document.getElementById('filt-forn')?.value || '').toLowerCase();
    const fs = document.getElementById('filt-sort')?.value || 'valor-desc';
    const onlySusp = document.getElementById('filt-suspeito')?.checked;

    let filtered = (DATA.despesas || []).filter((d) => {
      if (fp && d.nome_parlamentar !== fp) return false;
      if (fpartido && d.partido !== fpartido) return false;
      if (ft && d.tipo_despesa !== ft) return false;
      if (ff && !(d.fornecedor || '').toLowerCase().includes(ff) && !(d.cnpj_fornecedor || '').includes(ff))
        return false;
      if (onlySusp) {
        const en = DATA.fornecedoresEnrich[d.cnpj_fornecedor];
        if (!(d.suspeita || (en && (en.situacao === 'IRREGULAR' || en.sancionado_tcu)))) return false;
      }
      return true;
    });

    if (fs === 'valor-desc') filtered.sort((a, b) => b.valor - a.valor);
    else if (fs === 'valor-asc') filtered.sort((a, b) => a.valor - b.valor);
    else if (fs === 'data-desc')
      filtered.sort((a, b) => (b.data_documento || '').localeCompare(a.data_documento || ''));
    else filtered.sort((a, b) => (a.nome_parlamentar || '').localeCompare(b.nome_parlamentar || ''));

    const total = filtered.reduce((s, d) => s + (d.valor || 0), 0);
    const tag = document.getElementById('desp-tag');
    if (tag)
      tag.textContent = `${filtered.length} de ${(DATA.despesas || []).length} • ${fmt(total)}${
        DATA.despesasCountHint || ''
      }`;

    const start = (currentPageGastos - 1) * ITEMS_PER_PAGE;
    const pageData = filtered.slice(start, start + ITEMS_PER_PAGE);
    document.getElementById('tbl-body').innerHTML = pageData
      .map((d) => {
        const alertsStr =
          d.alertas && d.alertas.length
            ? `<br><span class="alert-mini">${esc(d.alertas.map((a) => a.tipo).join(', '))}</span>`
            : '';
        const en = DATA.fornecedoresEnrich[d.cnpj_fornecedor];
        const badge = enBadge(en);
        return `<tr class="${d.suspeita ? 'suspeito' : ''}">
        <td><b>${esc(d.nome_parlamentar)}</b><br><span class="sub">${esc(d.partido || '')}/${esc(d.uf || '')} • ${
          d.casa === 'camara' ? 'Câmara' : 'Senado'
        }</span></td>
        <td class="tipo-cell">${esc(d.tipo_despesa || '')}${alertsStr}</td>
        <td>${esc(d.fornecedor || 'N/A')} ${badge}</td>
        <td class="cnpj">${esc(d.cnpj_fornecedor || 'N/A')}</td>
        <td>${esc((d.data_documento || '').split('T')[0] || 'S/ Data')}<br>${docLink(d.url_documento, 'Nota')}</td>
        <td class="val ${valClass(d.valor)}">${esc(fmt(d.valor))}</td>
      </tr>`;
      })
      .join('');
    renderPag('pag-gastos', filtered.length, currentPageGastos, 'gastos', ITEMS_PER_PAGE);
  }

  function enBadge(en) {
    if (!en) return '<span class="badge-soft">N/V</span>';
    if (en.sancionado_tcu) return '<span class="badge-bad">TCU</span>';
    if (en.situacao === 'IRREGULAR') return '<span class="badge-bad">Irregular</span>';
    if (en.situacao === 'ATIVA' || en.situacao === 'REGULAR') return '<span class="badge-ok">Regular</span>';
    return `<span class="badge-soft">${esc(en.situacao || 'N/V')}</span>`;
  }

  /* ---------- Fornecedores ---------- */
  function renderFornecedores() {
    const f = DATA.top_fornecedores || [];
    document.getElementById('forn-tag').textContent = `${f.length} fornecedores`;
    document.getElementById('forn-list').innerHTML = f
      .map((x, i) => {
        const badge = enBadge(x.enrich);
        return `<div class="forn-card" role="button" tabindex="0" data-forn-filter="${esc(x.fornecedor)}" style="animation-delay:${i * 0.02}s">
        <div>
          <div class="nm">${i + 1}. ${esc(x.fornecedor)} ${badge}</div>
          <div class="cn">${esc(x.cnpj_fornecedor || '')}</div>
          <div class="parls">${esc(x.parlamentares)}</div>
        </div>
        <div>
          <div class="vl">${esc(fmt(x.total))}</div>
          <div class="qt">${x.qtd} nota(s)</div>
        </div>
      </div>`;
      })
      .join('');
    document.querySelectorAll('[data-forn-filter]').forEach((card) => {
      card.addEventListener('click', () => {
        const name = card.getAttribute('data-forn-filter');
        showTab('gastos');
        const inp = document.getElementById('filt-forn');
        if (inp) {
          inp.value = name;
          filterGastos(true);
        }
      });
    });
  }

  /* ---------- TSE ---------- */
  function renderTransparencia() {
    const ct = DATA.candidaturas_tse || [];
    if (!ct.length) {
      document.getElementById('transp-list').innerHTML =
        '<p class="empty-msg">Dados do TSE não disponíveis para este conjunto.</p>';
      return;
    }
    const parlMap = {};
    (DATA.parlamentares || []).forEach((p) => {
      const d = p.detalhes || {};
      if (d.score_transparencia) parlMap[p.nome_parlamentar] = d.score_transparencia;
    });
    document.getElementById('transp-list').innerHTML = ct
      .slice()
      .sort((a, b) => (b.total_bens || 0) - (a.total_bens || 0))
      .map((c) => {
        const t = parlMap[c.nome_parlamentar] || {};
        const sc_d = t.score_declaracao || 0;
        const sc_p = t.score_patrimonio || 0;
        const sc_r = t.score_receitas || 0;
        const sc_t = t.score || 0;
        return `<div class="forn-card">
        <div>
          <div class="nm">${esc(c.nome_parlamentar)} <span class="sub">${esc(c.cargo || '')} • ${esc(
          c.ano_eleicao || ''
        )}</span></div>
          <div class="score-break">
            <span>Declaração <b class="${sc(sc_d)}">${Number(sc_d).toFixed(0)}</b></span>
            <span>Patrimônio <b class="${sc(sc_p)}">${Number(sc_p).toFixed(0)}</b></span>
            <span>Receitas <b class="${sc(sc_r)}">${Number(sc_r).toFixed(0)}</b></span>
            <span>Total <b class="${sc(sc_t)}">${Number(sc_t).toFixed(1)}</b></span>
          </div>
        </div>
        <div>
          <div class="vl" style="color:var(--cyan)">${esc(fmt(c.total_bens || 0))}</div>
          <div class="qt">Patrimônio declarado</div>
        </div>
      </div>`;
      })
      .join('');
  }

  /* ---------- Votações ---------- */
  function renderVotacoes() {
    const votos = DATA.votos || [];
    const parls = [...new Set(votos.map((v) => v.nome_parlamentar).filter(Boolean))].sort();
    const partidos = [...new Set(votos.map((v) => v.partido).filter(Boolean))].sort();
    document.getElementById('filt-vot-parl').innerHTML =
      '<option value="">Todos os parlamentares</option>' +
      parls.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
    document.getElementById('filt-vot-part').innerHTML =
      '<option value="">Todos os partidos</option>' +
      partidos.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
    currentPageVotacoes = 1;
    filterVotacoes(false);
  }

  function filterVotacoes(resetPage) {
    if (resetPage !== false) currentPageVotacoes = 1;
    const fp = document.getElementById('filt-vot-parl')?.value || '';
    const fpartido = document.getElementById('filt-vot-part')?.value || '';
    const fv = document.getElementById('filt-vot-voto')?.value || '';
    const fd = (document.getElementById('filt-vot-desc')?.value || '').toLowerCase();
    const onlyDiv = document.getElementById('filt-vot-divergencia')?.checked;

    let filtered = (DATA.votos || []).filter((v) => {
      if (fp && v.nome_parlamentar !== fp) return false;
      if (fpartido && v.partido !== fpartido) return false;
      if (fv && v.voto !== fv) return false;
      if (fd && !(v.projeto_assunto || '').toLowerCase().includes(fd)) return false;
      if (onlyDiv && !v.divergiu_orientacao) return false;
      return true;
    });

    document.getElementById('vot-tag').textContent = `${filtered.length} votos${
      DATA.votosCountHint || ''
    }`;
    const start = (currentPageVotacoes - 1) * ITEMS_PER_PAGE;
    const pageData = filtered.slice(start, start + ITEMS_PER_PAGE);
    document.getElementById('tbl-body-votacoes').innerHTML = pageData
      .map((v) => {
        let color = 'var(--t3)';
        let bg = 'transparent';
        if (v.voto === 'Sim') {
          color = '#10b981';
          bg = 'rgba(16,185,129,0.1)';
        }
        if (v.voto === 'Não') {
          color = '#ef4444';
          bg = 'rgba(239,68,68,0.1)';
        }
        const div =
          v.divergiu_orientacao
            ? '<br><small class="alert-mini">Divergiu da orientação</small>'
            : '';
        return `<tr>
        <td><b>${esc(v.nome_parlamentar)}</b><br><span class="sub">${esc(v.partido || '')}/${esc(v.uf || '')}</span></td>
        <td class="sub">${v.casa === 'camara' ? 'Câmara' : 'Senado'}</td>
        <td>${esc(v.projeto_assunto || '')}</td>
        <td class="sub">${esc((v.data || '').split('T')[0] || '')}</td>
        <td style="font-weight:700;color:${color}"><span style="background:${bg};padding:4px 8px;border-radius:4px">${esc(
          v.voto || ''
        )}</span>${div}${
          v.descricao_ausencia ? `<br><small class="sub">${esc(v.descricao_ausencia)}</small>` : ''
        }</td>
      </tr>`;
      })
      .join('');
    renderPag('pag-votacoes', filtered.length, currentPageVotacoes, 'votacoes', ITEMS_PER_PAGE);
  }

  /* ---------- Proposições ---------- */
  function renderProposicoes() {
    const props = DATA.proposicoes || [];
    document.getElementById('prop-tag').textContent = `${props.length} proposições`;
    const parls = [...new Set(props.map((p) => p.nome_parlamentar).filter(Boolean))].sort();
    const sel = document.getElementById('filt-prop-parl');
    if (sel)
      sel.innerHTML =
        '<option value="">Todos</option>' +
        parls.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
    currentPageProp = 1;
    filterProposicoes(false);
  }

  function filterProposicoes(resetPage) {
    if (resetPage !== false) currentPageProp = 1;
    const fp = document.getElementById('filt-prop-parl')?.value || '';
    const fq = (document.getElementById('filt-prop-q')?.value || '').toLowerCase();
    let filtered = (DATA.proposicoes || []).filter((p) => {
      if (fp && p.nome_parlamentar !== fp) return false;
      if (
        fq &&
        !(p.ementa || '').toLowerCase().includes(fq) &&
        !(p.sigla_tipo || '').toLowerCase().includes(fq) &&
        !(p.numero || '').toString().includes(fq)
      )
        return false;
      return true;
    });
    const start = (currentPageProp - 1) * ITEMS_PER_PAGE;
    const page = filtered.slice(start, start + ITEMS_PER_PAGE);
    const body = document.getElementById('tbl-body-proposicoes');
    if (!filtered.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty-msg">Nenhuma proposição carregada. Execute o ETL de proposições.</td></tr>`;
    } else {
      body.innerHTML = page
        .map((p) => {
          const link = docLink(p.url_oficial, 'Abrir');
          return `<tr>
          <td><b>${esc(p.nome_parlamentar || '—')}</b></td>
          <td>${esc(p.sigla_tipo || '')} ${esc(p.numero || '')}/${esc(p.ano || '')}</td>
          <td>${esc((p.ementa || '').substring(0, 160))}${(p.ementa || '').length > 160 ? '…' : ''}</td>
          <td class="sub">${esc(p.situacao || '')}</td>
          <td>${link}</td>
        </tr>`;
        })
        .join('');
    }
    renderPag('pag-proposicoes', filtered.length, currentPageProp, 'proposicoes', ITEMS_PER_PAGE);
  }

  /* ---------- Auditoria / Metodologia ---------- */
  function renderAuditoria() {
    const alertas = DATA.alertas_auditoria || [];
    document.getElementById('auditoria-tag').textContent = auditComputed
      ? `${alertas.length} alertas`
      : 'Calculando…';
    if (!auditComputed) {
      document.getElementById('auditoria-list').innerHTML =
        '<p class="empty-msg">Calculando alertas…</p>';
      return;
    }
    if (!alertas.length) {
      document.getElementById('auditoria-list').innerHTML =
        '<p class="empty-ok">Nenhum gasto duvidoso detectado neste recorte.</p>';
      return;
    }
    const sorted = alertas.slice().sort((a, b) => (b.valor || 0) - (a.valor || 0)).slice(0, 200);
    document.getElementById('auditoria-list').innerHTML = sorted
      .map(
        (a) => `<div class="forn-card audit-card">
      <div>
        <div class="nm">${esc(a.nome_parlamentar)} <span class="sub">• ${esc(a.tipo_despesa || '')}</span></div>
        <div class="alert-mini">${esc(a.tipo)}</div>
        <div class="parls">${esc(a.descricao || '')}</div>
        <div class="parls"><strong>Fornecedor:</strong> ${esc(a.fornecedor || 'N/A')}
          <br><small class="mono">${esc(a.cnpj_fornecedor || 'N/A')}</small>
          <br><strong>Data:</strong> ${esc((a.data_documento || '').split('T')[0] || 'N/A')} ${docLink(
          a.url_documento,
          'Comprovante'
        )}</div>
      </div>
      <div class="vl" style="color:var(--red)">${esc(fmt(a.valor))}</div>
    </div>`
      )
      .join('');
  }

  function renderMetodologia() {
    const el = document.getElementById('metodologia-body');
    if (!el) return;
    el.innerHTML = `
      <article class="method-block">
        <h3>Nota CVP-IA</h3>
        <p>Média ponderada de presença em votações, economia da cota (CEAP), transparência TSE e coerência partidária. Períodos sem despesas e sem votos são marcados como <strong>dados insuficientes</strong> e não devem ser lidos como “nota média”.</p>
      </article>
      <article class="method-block">
        <h3>Alertas de auditoria</h3>
        <ul>
          <li><strong>Valor redondo alto</strong> — valor ≥ R$ 5.000 e múltiplo de 1.000</li>
          <li><strong>Refeição excessiva</strong> — alimentação &gt; R$ 200</li>
          <li><strong>Fim de semana</strong> — despesa em sáb/dom fora de hospedagem/passagem/combustível/locação</li>
          <li><strong>Fracionamento</strong> — ≥ 3 notas no mesmo dia para o mesmo CNPJ</li>
          <li><strong>CNPJ/TCU</strong> — fornecedor irregular ou sancionado (quando enriquecido pelo ETL)</li>
        </ul>
        <p>Heurísticas auxiliam triagem; não comprovam irregularidade. Valide sempre o comprovante oficial.</p>
      </article>
      <article class="method-block">
        <h3>Fontes</h3>
        <p>APIs de dados abertos da Câmara, Senado e TSE. Atualização via pipeline diário → Supabase.</p>
      </article>`;
  }

  /* ---------- Ficha unificada ---------- */
  function openFicha(parlamentarId) {
    const p = (DATA.parlamentares || []).find((x) => String(x.id) === String(parlamentarId));
    if (!p) return;
    const drawer = document.getElementById('ficha-drawer');
    const body = document.getElementById('ficha-body');
    const desps = (DATA.despesas || []).filter((d) => String(d.parlamentar_id) === String(p.id));
    const votos = (DATA.votos || []).filter((v) => v.nome_parlamentar === p.nome_parlamentar);
    const props = (DATA.proposicoes || []).filter((x) => x.nome_parlamentar === p.nome_parlamentar);
    const tse = (DATA.candidaturas_tse || []).find((c) => c.nome_parlamentar === p.nome_parlamentar);
    const divergencias = votos.filter((v) => v.divergiu_orientacao);
    const totalG = desps.reduce((s, d) => s + (d.valor || 0), 0);
    const histScores = (DATA.allScores || [])
      .filter((s) => String(s.parlamentar_id) === String(p.id))
      .sort((a, b) => parsePeriod(a.periodo) - parsePeriod(b.periodo));

    const official =
      p.casa === 'camara'
        ? `https://www.camara.leg.br/deputados/${esc(p.id_api || p.id)}`
        : `https://www25.senado.leg.br/web/senadores/senador/-/perfil/${esc(p.id_api || p.id)}`;

    body.innerHTML = `
      <header class="ficha-head">
        <h2>${esc(p.nome_parlamentar)}</h2>
        <p>${p.casa === 'camara' ? 'Câmara' : 'Senado'} • ${esc(p.partido || '')}/${esc(p.uf || '')}
          • Período: ${esc(periodLabel(p.periodo) || currentPeriod)}</p>
        <div class="score-break">
          <span>Nota <b class="${sc(p.score_geral || 0)}">${Number(p.score_geral || 0).toFixed(1)}</b></span>
          <span>Presença <b class="${sc(p.score_atividade || 0)}">${Number(p.score_atividade || 0).toFixed(0)}</b></span>
          <span>Economia <b class="${sc(p.score_gasto || 0)}">${Number(p.score_gasto || 0).toFixed(0)}</b></span>
          <span>Transp. <b class="${sc(p.score_transparencia || 0)}">${Number(p.score_transparencia || 0).toFixed(0)}</b></span>
          <span>Coerência <b class="${sc(p.score_coerencia || 0)}">${Number(p.score_coerencia || 0).toFixed(0)}</b></span>
        </div>
        <p><a href="${safeUrl(official) || '#'}" target="_blank" rel="noopener noreferrer">Perfil oficial</a></p>
      </header>
      <section>
        <h3>Histórico de scores</h3>
        ${
          histScores.length
            ? `<ul class="hist-list">${histScores
                .map(
                  (s) =>
                    `<li>${esc(s.periodo)} — <b class="${sc(s.score_geral || 0)}">${Number(
                      s.score_geral || 0
                    ).toFixed(1)}</b></li>`
                )
                .join('')}</ul>`
            : '<p class="sub">Sem histórico adicional.</p>'
        }
      </section>
      <section>
        <h3>Despesas (${desps.length}) — ${esc(fmt(totalG))}</h3>
        <div class="exp-grid compact">${desps
          .slice()
          .sort((a, b) => b.valor - a.valor)
          .slice(0, 15)
          .map(
            (d) =>
              `<div class="exp-row"><div>${esc((d.tipo_despesa || '').substring(0, 28))}</div><div>${esc(
                d.fornecedor || ''
              )}</div><div>${docLink(d.url_documento, 'Nota')}</div><div class="val">${esc(
                fmt(d.valor)
              )}</div></div>`
          )
          .join('')}</div>
      </section>
      <section>
        <h3>Votos recentes (${votos.length}) ${
      divergencias.length ? `• ${divergencias.length} divergência(s)` : ''
    }</h3>
        <ul class="hist-list">${votos
          .slice(0, 12)
          .map(
            (v) =>
              `<li>${esc((v.data || '').split('T')[0])} — ${esc(v.voto)} — ${esc(
                (v.projeto_assunto || '').substring(0, 80)
              )}${v.divergiu_orientacao ? ' <span class="alert-mini">divergência</span>' : ''}</li>`
          )
          .join('')}</ul>
      </section>
      <section>
        <h3>Proposições (${props.length})</h3>
        ${
          props.length
            ? `<ul class="hist-list">${props
                .slice(0, 10)
                .map(
                  (x) =>
                    `<li>${esc(x.sigla_tipo || '')} ${esc(x.numero || '')}/${esc(x.ano || '')} — ${esc(
                      (x.ementa || '').substring(0, 100)
                    )}</li>`
                )
                .join('')}</ul>`
            : '<p class="sub">Sem proposições neste recorte.</p>'
        }
      </section>
      <section>
        <h3>TSE</h3>
        ${
          tse
            ? `<p>Bens: <b>${esc(fmt(tse.total_bens || 0))}</b>${
                tse.total_receitas ? ` • Receitas: <b>${esc(fmt(tse.total_receitas))}</b>` : ''
              }</p>`
            : '<p class="sub">Sem candidatura TSE vinculada.</p>'
        }
      </section>`;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    document.getElementById('ficha-backdrop').classList.add('open');
  }

  function closeFicha() {
    document.getElementById('ficha-drawer').classList.remove('open');
    document.getElementById('ficha-drawer').setAttribute('aria-hidden', 'true');
    document.getElementById('ficha-backdrop').classList.remove('open');
  }

  /* ---------- Pagination ---------- */
  function renderPag(containerId, totalItems, currentPage, kind, perPage) {
    const totalPages = Math.ceil(totalItems / perPage) || 1;
    const container = document.getElementById(containerId);
    if (!container) return;
    if (totalPages <= 1) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = `
      <button type="button" class="pag-btn" data-pag="${kind}" data-dir="-1" ${
      currentPage === 1 ? 'disabled' : ''
    }>Anterior</button>
      <span class="pag-info">Página ${currentPage} de ${totalPages}</span>
      <button type="button" class="pag-btn" data-pag="${kind}" data-dir="1" ${
      currentPage === totalPages ? 'disabled' : ''
    }>Próxima</button>`;
    container.querySelectorAll('[data-pag]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const dir = Number(btn.getAttribute('data-dir'));
        const k = btn.getAttribute('data-pag');
        if (k === 'ranking') {
          currentPageRanking += dir;
          renderRanking(false);
        } else if (k === 'gastos') {
          currentPageGastos += dir;
          filterGastos(false);
        } else if (k === 'votacoes') {
          currentPageVotacoes += dir;
          filterVotacoes(false);
        } else if (k === 'proposicoes') {
          currentPageProp += dir;
          filterProposicoes(false);
        }
        document.querySelector('.main-content').scrollTop = 0;
      });
    });
  }

  function refreshFromPeriod() {
    auditComputed = false;
    DATA.parlamentares = buildParlamentaresFromScores(DATA.allScores || []);
    DATA.despesas = filterDespesasByPeriod(DATA.allDespesas || []);
    DATA.votos = filterVotosByPeriod(DATA.allVotos || []);
    DATA.total_gasto = DATA.despesas.reduce((s, d) => s + (d.valor || 0), 0);
    DATA.top_fornecedores = buildTopFornecedores(DATA.despesas);
    renderAll();
  }

  function renderAll() {
    renderPeriodSelect();
    renderStats();
    renderRanking(true);
    renderGastos();
    renderFornecedores();
    renderTransparencia();
    renderVotacoes();
    renderProposicoes();
    if (activeTab === 'auditoria') {
      computeAuditoriaLazy().then(() => renderAuditoria());
    } else {
      document.getElementById('auditoria-tag').textContent = 'Abra a aba para calcular';
      document.getElementById('auditoria-list').innerHTML =
        '<p class="empty-msg">Abra esta aba para calcular alertas (processamento sob demanda).</p>';
    }
    const updated = document.getElementById('updated');
    if (updated && DATA.loadedAt) {
      updated.textContent = DATA.loadedAt.toLocaleString('pt-BR');
    }
  }

  /* ---------- Load ---------- */
  async function fetchAllPages(table, select, pageSize) {
    let page = 0;
    let out = [];
    const maxPages = 40;
    while (page < maxPages) {
      const from = page * pageSize;
      const to = from + pageSize - 1;
      const { data, error } = await supabase.from(table).select(select).range(from, to);
      if (error) throw error;
      if (!data || !data.length) break;
      out = out.concat(data);
      if (data.length < pageSize) break;
      page++;
    }
    return out;
  }

  async function loadFromSupabase() {
    const ranking = document.getElementById('ranking');
    ranking.innerHTML = '<p class="empty-msg">Carregando dados da nuvem…</p>';
    try {
      if (!window.supabase) throw new Error('SDK Supabase não carregado.');
      supabase = window.supabase.createClient(SUPA_URL, SUPA_KEY);

      const { data: scoresData, error: err1 } = await supabase
        .from('scores')
        .select('*, parlamentares(nome_parlamentar, partido, uf, casa, id, id_api)')
        .order('periodo', { ascending: false });
      if (err1) throw err1;

      DATA.allScores = scoresData || [];
      const periods = [
        ...new Set(
          (scoresData || [])
            .map((s) => s.periodo)
            .filter(Boolean)
            .map((p) => String(p).replace('-', '/'))
        ),
      ].sort((a, b) => parsePeriod(b) - parsePeriod(a));
      DATA.periods = periods;

      const despData = await fetchAllPages(
        'despesas',
        '*, parlamentares(nome_parlamentar, partido, uf, casa)',
        1000
      );
      DATA.allDespesas = despData.map((d) => {
        if (d.parlamentares) {
          d.nome_parlamentar = d.parlamentares.nome_parlamentar;
          d.partido = d.parlamentares.partido;
          d.uf = d.parlamentares.uf;
          d.casa = d.parlamentares.casa;
        }
        d.alertas = [];
        d.suspeita = false;
        return d;
      });
      if (despData.length >= 40000) {
        DATA.despesasCountHint = ' (limite de paginação atingido)';
      }

      let fornRows = [];
      try {
        const { data: fr } = await supabase.from('fornecedores').select('*').limit(5000);
        fornRows = fr || [];
      } catch (_) {
        fornRows = [];
      }
      DATA.fornecedoresEnrich = {};
      fornRows.forEach((f) => {
        if (f.cnpj) DATA.fornecedoresEnrich[f.cnpj] = f;
      });

      let propRows = [];
      try {
        const props = await fetchAllPages(
          'proposicoes',
          '*, parlamentares(nome_parlamentar)',
          1000
        );
        propRows = props.map((p) => {
          if (p.parlamentares) p.nome_parlamentar = p.parlamentares.nome_parlamentar;
          return p;
        });
      } catch (_) {
        propRows = [];
      }
      DATA.proposicoes = propRows;

      const { data: tseData } = await supabase
        .from('candidaturas_tse')
        .select('*, parlamentares(nome_parlamentar)');
      DATA.candidaturas_tse = (tseData || []).map((c) => {
        if (c.parlamentares) c.nome_parlamentar = c.parlamentares.nome_parlamentar;
        return c;
      });

      let votosRaw = [];
      try {
        votosRaw = await fetchAllPages(
          'votos',
          `voto, descricao_ausencia, divergiu_orientacao,
           votacoes(descricao, data, casa),
           parlamentares(nome_parlamentar, partido, uf)`,
          1000
        );
      } catch (_) {
        const { data } = await supabase
          .from('votos')
          .select(
            `voto, descricao_ausencia, votacoes(descricao, data, casa), parlamentares(nome_parlamentar, partido, uf)`
          )
          .limit(5000);
        votosRaw = data || [];
        DATA.votosCountHint = ' (amostra)';
      }

      DATA.allVotos = (votosRaw || []).map((v) => ({
        voto: v.voto,
        descricao_ausencia: v.descricao_ausencia,
        divergiu_orientacao: !!v.divergiu_orientacao,
        projeto_assunto: v.votacoes ? v.votacoes.descricao : 'S/N',
        data: v.votacoes ? v.votacoes.data : '',
        casa: v.votacoes ? v.votacoes.casa : '',
        nome_parlamentar: v.parlamentares ? v.parlamentares.nome_parlamentar : 'S/N',
        partido: v.parlamentares ? v.parlamentares.partido : '',
        uf: v.parlamentares ? v.parlamentares.uf : '',
      }));

      try {
        const { data: runs } = await supabase
          .from('pipeline_runs')
          .select('atualizado_em')
          .order('atualizado_em', { ascending: false })
          .limit(1);
        if (runs && runs[0] && runs[0].atualizado_em) {
          DATA.loadedAt = new Date(runs[0].atualizado_em);
        } else {
          DATA.loadedAt = new Date();
        }
      } catch (_) {
        DATA.loadedAt = new Date();
      }

      document.getElementById('status-badge').textContent = 'CVP-IA • Dados da nuvem';
      refreshFromPeriod();
    } catch (e) {
      console.error(e);
      ranking.innerHTML = `<h3 class="error-msg">Erro ao carregar: ${esc(e.message || e)}</h3>`;
    }
  }

  function bindFiltersOnce() {
    if (filtersBound) return;
    filtersBound = true;

    document.querySelectorAll('.tab[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => showTab(btn.getAttribute('data-tab')));
    });

    ['filt-parl', 'filt-partido', 'filt-tipo', 'filt-sort'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => filterGastos(true));
    });
    const forn = document.getElementById('filt-forn');
    if (forn) forn.addEventListener('input', debounce(() => filterGastos(true), 250));
    const sus = document.getElementById('filt-suspeito');
    if (sus) sus.addEventListener('change', () => filterGastos(true));

    ['filt-vot-parl', 'filt-vot-part', 'filt-vot-voto'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => filterVotacoes(true));
    });
    const vd = document.getElementById('filt-vot-desc');
    if (vd) vd.addEventListener('input', debounce(() => filterVotacoes(true), 250));
    const div = document.getElementById('filt-vot-divergencia');
    if (div) div.addEventListener('change', () => filterVotacoes(true));

    const pp = document.getElementById('filt-prop-parl');
    if (pp) pp.addEventListener('change', () => filterProposicoes(true));
    const pq = document.getElementById('filt-prop-q');
    if (pq) pq.addEventListener('input', debounce(() => filterProposicoes(true), 250));

    const periodo = document.getElementById('filt-periodo');
    if (periodo)
      periodo.addEventListener('change', () => {
        currentPeriod = periodo.value;
        refreshFromPeriod();
      });

    const global = document.getElementById('filt-busca-global');
    if (global)
      global.addEventListener(
        'input',
        debounce(() => {
          searchQuery = global.value.trim();
          renderStats();
          renderRanking(true);
        }, 250)
      );

    document.getElementById('ficha-close')?.addEventListener('click', closeFicha);
    document.getElementById('ficha-backdrop')?.addEventListener('click', closeFicha);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeFicha();
    });
  }

  window.addEventListener('DOMContentLoaded', () => {
    bindFiltersOnce();
    loadFromSupabase();
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('sw.js').catch((err) => console.error('SW Fail', err));
    }
  });

  // Expose minimal API for inline fallbacks (none required if data-tab used)
  window.CVP = { showTab, openFicha, closeFicha, esc, safeUrl };
})();
