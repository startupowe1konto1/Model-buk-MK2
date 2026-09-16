const $ = id => document.getElementById(id);
const pct = x => x === null || x === undefined ? '—' : `${(Number(x) * 100).toFixed(1)}%`;
const odd = x => x === null || x === undefined ? '—' : Number(x).toFixed(2);
const num = x => x === null || x === undefined ? '—' : Number(x).toFixed(2);
const pp = x => x === null || x === undefined ? '—' : `${(Number(x) * 100).toFixed(1)} pp`;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

let state = {status:null, leagues:[], selectedLeagues:new Set(), analysis:null, marketGroup:'goals'};

async function api(path, options={}) {
  const r = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  let data;
  try { data = await r.json(); } catch { data = {}; }
  if (!r.ok) throw new Error(data.detail || `Błąd API (${r.status})`);
  return data;
}

function todayLocal() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,10);
}
function tomorrowHour() {
  const d = new Date(Date.now()+86400000); d.setMinutes(0,0,0);
  return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);
}

async function loadStatus() {
  try {
    const s = await api('/api/status'); state.status=s;
    const live = s.live_provider || {};
    const chip=$('providerChip');
    chip.textContent = live.connected ? `LIVE: ${live.name}` : 'LIVE DATA: niepodłączone';
    chip.className = `status-chip ${live.connected?'ok':'warn'}`;
    const stats = [
      ['Wersja', s.app_version || 'v0.5'],
      ['Tryb', s.deployment_status || 'research'],
      ['Ligi', s.leagues ?? '—'],
      ['Historia', s.historical_matches ? `${Number(s.historical_matches).toLocaleString('pl-PL')} meczów` : '—'],
      ['Rynki', (s.market_engines || []).join(', ') || '—'],
      ['Live provider', live.connected ? 'podłączony' : 'brak klucza'],
      ['EPL corners champion', s.epl_corner_champion_available ? 'dostępny' : 'brak'],
    ];
    $('systemStats').innerHTML = stats.map(([a,b])=>`<div class="system-card"><span>${esc(a)}</span><strong>${esc(b)}</strong></div>`).join('');
  } catch(e) {
    $('providerChip').textContent='System offline'; $('providerChip').className='status-chip warn';
    $('systemStats').innerHTML=`<div class="error">${esc(e.message)}</div>`;
  }
}

async function loadLeagues() {
  const {leagues}=await api('/api/catalog/leagues'); state.leagues=leagues;
  if (!state.selectedLeagues.size) leagues.forEach(l=>state.selectedLeagues.add(l.code));
  $('leagueFilters').innerHTML = leagues.map(l=>`<button class="league-chip active" data-code="${l.code}">${esc(l.name)}</button>`).join('');
  $('manualLeague').innerHTML = leagues.map(l=>`<option value="${l.code}">${esc(l.name)}</option>`).join('');
  document.querySelectorAll('.league-chip').forEach(btn=>btn.addEventListener('click',()=>{
    const code=btn.dataset.code;
    if(state.selectedLeagues.has(code)){state.selectedLeagues.delete(code);btn.classList.remove('active')}else{state.selectedLeagues.add(code);btn.classList.add('active')}
  }));
  await loadManualTeams();
}

async function loadManualTeams() {
  const league=$('manualLeague').value;
  const {teams}=await api(`/api/teams?league=${encodeURIComponent(league)}`);
  const html='<option value="">Wybierz drużynę</option>'+teams.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');
  $('manualHome').innerHTML=html; $('manualAway').innerHTML=html;
}

function renderFixtures(data){
  const list=$('fixturesList'); const notice=$('liveNotice');
  if(data.mode!=='live'){
    notice.classList.remove('hidden'); notice.innerHTML=`<b>Live data nie jest jeszcze podłączone.</b> ${esc(data.message||'')} Możesz już analizować mecze ręcznie poniżej.`;
    list.innerHTML='<div class="empty-state">Po podłączeniu API-Football tutaj pojawią się automatycznie mecze z wybranego dnia i lig.</div>';
    return;
  }
  notice.classList.add('hidden');
  const rows=data.fixtures||[];
  if(!rows.length){list.innerHTML='<div class="empty-state">Brak meczów dla wybranych lig i dnia.</div>';return;}
  list.innerHTML=rows.map(f=>`<button class="fixture-card" data-id="${f.fixture_id}">
    <div class="fixture-top"><span>${esc(f.league_name||f.league_code)}</span><span>${esc(String(f.kickoff||'').slice(11,16))}</span></div>
    <div class="team-row"><img src="${esc(f.home.logo||'')}" alt=""><b>${esc(f.home.name)}</b></div>
    <div class="team-row"><img src="${esc(f.away.logo||'')}" alt=""><b>${esc(f.away.name)}</b></div>
    <div class="fixture-bottom"><span>${esc(f.round||'')}</span><strong>Analizuj →</strong></div>
  </button>`).join('');
  document.querySelectorAll('.fixture-card').forEach(card=>card.addEventListener('click',()=>analyzeFixture(Number(card.dataset.id))));
}

async function loadFixtures(){
  const btn=$('refreshFixtures'); btn.disabled=true; btn.textContent='Pobieranie…';
  try{
    const date=$('fixtureDay').value;
    const qs=[...state.selectedLeagues].map(x=>`league=${encodeURIComponent(x)}`).join('&');
    const data=await api(`/api/fixtures?date=${encodeURIComponent(date)}${qs?'&'+qs:''}`);
    renderFixtures(data);
  }catch(e){$('liveNotice').classList.remove('hidden');$('liveNotice').innerHTML=`<b>Błąd live data:</b> ${esc(e.message)}`}
  finally{btn.disabled=false;btn.textContent='Pobierz mecze'}
}

function expectedCard(idTotal,idTeams,obj){
  $(idTotal).textContent=num(obj?.total); $(idTeams).textContent=`${num(obj?.home)} — ${num(obj?.away)}`;
}

function marketGroups(analysis){
  const groups=[
    ['result','1X2'],['btts','BTTS'],['goals','Gole'],['corners','Rożne'],['shots','Strzały'],['sot','SOT'],['cards','Kartki']
  ];
  $('marketTabs').innerHTML=groups.map(([k,l])=>`<button class="market-tab ${state.marketGroup===k?'active':''}" data-group="${k}">${l}</button>`).join('');
  document.querySelectorAll('.market-tab').forEach(btn=>btn.addEventListener('click',()=>{state.marketGroup=btn.dataset.group;marketGroups(analysis);renderMarketTable(analysis)}));
}

function renderMarketTable(a){
  const compared=new Map((a.market_comparison||[]).map(x=>[x.market_key,x]));
  const modelRows=(a.markets||[]).filter(m=>m.group===state.marketGroup);
  $('marketRows').innerHTML=modelRows.length?modelRows.map(m=>{
    const c=compared.get(m.market_key);
    const decision=c?.decision||'FAIR ONLY';
    return `<tr class="${decision==='BET'?'bet-row':''}">
      <td><strong>${esc(m.label)}</strong><small>${m.threshold!==null&&m.threshold!==undefined?`próg ${m.threshold}`:''}</small></td>
      <td>${pct(m.probability)}</td><td>${odd(m.fair_odds)}</td>
      <td>${esc(c?.bookmaker||'—')}</td><td>${odd(c?.odds)}</td><td>${pct(c?.market_probability)}</td>
      <td class="${c?.edge>0?'positive':''}">${pp(c?.edge)}</td><td class="${c?.ev>0?'positive':''}">${pct(c?.ev)}</td>
      <td><span class="decision-tag ${decision==='BET'?'bet':''}">${esc(decision)}</span></td>
    </tr>`
  }).join(''):'<tr><td colspan="9" class="muted">Brak rynków w tej kategorii.</td></tr>';
}

function renderOpportunities(a){
  const rows=a.opportunities||[];
  if(!rows.length){
    $('opportunityList').innerHTML=`<div class="empty-state compact">${a.raw_odds_count? 'Brak rynku spełniającego obecny edge + EV gate.' : 'Brak kursów z live feedu — pokazujemy fair odds, ale nie wydajemy decyzji BET.'}</div>`;
    return;
  }
  $('opportunityList').innerHTML=rows.slice(0,6).map(o=>`<div class="opportunity-card">
    <div class="opp-head"><span>${esc(o.group)}</span><b>BET</b></div>
    <h4>${esc(o.label)}</h4>
    <div class="opp-price"><strong>@ ${odd(o.odds)}</strong><span>${esc(o.bookmaker)}</span></div>
    <div class="opp-stats"><span>Model <b>${pct(o.probability)}</b></span><span>Fair <b>${odd(o.fair_odds)}</b></span><span>Edge <b>${pp(o.edge)}</b></span><span>EV <b>${pct(o.ev)}</b></span></div>
  </div>`).join('');
}

function factorValue(v){
  if(v===null||v===undefined)return '—';
  if(Array.isArray(v)) return v.join('');
  if(typeof v==='object') return Object.entries(v).filter(([,x])=>x!==null&&x!==undefined).map(([k,x])=>`${k}: ${Array.isArray(x)?x.join(''):x}`).join(' · ');
  return String(v);
}
function statusLabel(s){return ({model_input:'WEJŚCIE MODELU',context:'KONTEKST',quality_gate:'QUALITY GATE',confirmed:'POTWIERDZONE',not_confirmed:'NIEPOTWIERDZONE'})[s]||String(s||'KONTEKST').toUpperCase()}
function renderFactors(a){
  $('factorList').innerHTML=(a.factors||[]).map(f=>`<div class="factor-item">
    <div class="factor-top"><strong>${esc(f.label)}</strong><span class="factor-status ${f.status==='model_input'?'input':''}">${esc(statusLabel(f.status))}</span></div>
    <div class="factor-values">${f.home!==undefined?`<span>HOME <b>${esc(factorValue(f.home))}</b></span>`:''}${f.away!==undefined?`<span>AWAY <b>${esc(factorValue(f.away))}</b></span>`:''}${f.value!==undefined?`<span><b>${esc(factorValue(f.value))}</b></span>`:''}</div>
    ${f.explanation?`<p>${esc(f.explanation)}</p>`:''}
  </div>`).join('');
}

function renderContext(a){
  const c=a.current_context||{};
  if(!c.connected){$('contextList').innerHTML=`<div class="empty-state compact">${esc(c.message||'Brak bieżącego kontekstu.')}</div>`;return;}
  const lineups=c.lineups||[]; const injuries=c.injuries||[]; const w=c.weather||{}; const errors=c.errors||[];
  const rows=[
    ['Bieżące statystyki', a.current_data?.used_in_model?'użyte w modelu':'brak / za mało danych', a.current_data?.used_in_model?'ok':'warn'],
    ['Kontuzje / zawieszenia', `${injuries.length} rekordów`, errors.some(x=>String(x).startsWith('injuries:'))?'warn':'ok'],
    ['Składy', lineups.length>=2?'potwierdzone / dostępne':'jeszcze niedostępne', lineups.length>=2?'ok':'neutral'],
    ['Sędzia', a.fixture?.referee||'brak', a.fixture?.referee?'ok':'neutral'],
    ['Pogoda', w.available ? `${w.forecast?.temperature_c??'—'}°C · wiatr ${w.forecast?.wind_kmh??'—'} km/h${w.extreme?' · EXTREME':''}` : (w.reason||'brak'), w.extreme?'warn':w.available?'ok':'neutral'],
    ['Kursy', a.raw_odds_count?`${a.raw_odds_count} cen surowych`:'brak', a.raw_odds_count?'ok':'neutral'],
  ];
  $('contextList').innerHTML=rows.map(([k,v,s])=>`<div class="context-row"><span class="context-dot ${s}"></span><div><strong>${esc(k)}</strong><small>${esc(v)}</small></div></div>`).join('');
}

function renderBenchmarks(a){
  const blocks=[];
  if(a.benchmarks?.epl_corner_champion){const b=a.benchmarks.epl_corner_champion;blocks.push(`<div class="benchmark"><strong>EPL Corner Champion</strong><span>${esc(b.model||'')}</span><p>Expected total: <b>${num(b.expected_total)}</b>. ${esc(b.note||'')}</p></div>`)}
  const ext=a.current_context?.external_prediction_benchmark;
  if(ext){blocks.push(`<div class="benchmark"><strong>API-Football benchmark</strong><span>tylko porównanie</span><p>${esc(ext.advice||'')} · ${esc(ext.under_over||'')}</p></div>`)}
  $('benchmarkBlock').classList.toggle('hidden',!blocks.length); $('benchmarkList').innerHTML=blocks.join('');
}

function renderAnalysis(a){
  state.analysis=a;
  $('analysisPanel').classList.remove('hidden');
  $('analysisLeague').textContent=`${a.league?.name||''} · ${a.league?.country||''}`;
  $('analysisTitle').textContent=`${a.fixture.home_team} — ${a.fixture.away_team}`;
  $('analysisMeta').textContent=`${String(a.fixture.date||'').slice(0,16).replace('T',' ')}${a.fixture.round?' · '+a.fixture.round:''}`;
  const conf=a.confidence || {score:a.data_quality?.score,label:'baseline'};
  $('confidenceScore').textContent=conf.score!==undefined?`${Number(conf.score).toFixed(0)}/100`:'—'; $('confidenceLabel').textContent=conf.label||'';
  $('analysisMode').textContent=a.mode==='live_current_context'?'LIVE + HISTORY':'HISTORY BASELINE';
  const warn=a.data_quality?.warning; $('analysisWarning').classList.toggle('hidden',!warn); $('analysisWarning').textContent=warn||'';
  expectedCard('xGoals','xGoalsTeams',a.expected?.goals); expectedCard('xCorners','xCornersTeams',a.expected?.corners); expectedCard('xShots','xShotsTeams',a.expected?.shots); expectedCard('xSot','xSotTeams',a.expected?.sot); expectedCard('xCards','xCardsTeams',a.expected?.cards);
  renderOpportunities(a); marketGroups(a); renderMarketTable(a); renderFactors(a); renderContext(a); renderBenchmarks(a);
  $('analysisPanel').scrollIntoView({behavior:'smooth',block:'start'});
}

async function analyzeFixture(id){
  $('liveNotice').classList.add('hidden');
  try{
    $('providerChip').textContent='Analiza live…';
    const a=await api(`/api/analyze/fixture/${id}?deep=true&persist=true`,{method:'POST'}); renderAnalysis(a); await loadRecent();
  }catch(e){$('liveNotice').classList.remove('hidden');$('liveNotice').innerHTML=`<b>Nie udało się przeanalizować meczu:</b> ${esc(e.message)}`}
  finally{await loadStatus()}
}

async function loadRecent(){
  try{const {predictions}=await api('/api/predictions?limit=12'); $('recentList').innerHTML=predictions.length?predictions.map(p=>`<div class="recent-item"><div><strong>${esc(p.home_team)} — ${esc(p.away_team)}</strong><small>${esc(String(p.fixture_date).slice(0,16).replace('T',' '))} · #${p.id}</small></div><span>${esc(p.model_version)}</span><b class="${p.decision==='BET'?'positive':''}">${esc(p.decision||'PURE')}</b></div>`).join(''):'<div class="empty-state compact">Brak zapisanych predykcji.</div>'}catch(e){$('recentList').innerHTML=`<div class="error">${esc(e.message)}</div>`}
}

$('manualLeague').addEventListener('change',loadManualTeams);
$('manualForm').addEventListener('submit',async e=>{
  e.preventDefault(); const err=$('manualError'); const btn=$('manualAnalyze'); err.classList.add('hidden'); btn.disabled=true; btn.textContent='Analiza…';
  try{const body={league_code:$('manualLeague').value,date:$('manualDate').value,home_team:$('manualHome').value,away_team:$('manualAway').value,persist:true}; const a=await api('/api/analyze/manual',{method:'POST',body:JSON.stringify(body)}); renderAnalysis(a); await loadRecent()}catch(ex){err.textContent=ex.message;err.classList.remove('hidden')}finally{btn.disabled=false;btn.textContent='Analizuj mecz'}
});
$('refreshFixtures').addEventListener('click',loadFixtures); $('refreshRecent').addEventListener('click',loadRecent);
document.querySelectorAll('.nav-item').forEach(btn=>btn.addEventListener('click',()=>document.getElementById(btn.dataset.scroll)?.scrollIntoView({behavior:'smooth'})));

$('fixtureDay').value=todayLocal(); $('manualDate').value=tomorrowHour();
Promise.all([loadStatus(),loadLeagues(),loadRecent()]).then(loadFixtures).catch(e=>console.error(e));
