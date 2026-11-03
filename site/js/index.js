const $ = (id) => document.getElementById(id);

// Cameroon's matches in the data, with what made each one worth watching
const STORY = [
  { id: 3857280, note: "Group stage, last game. Vincent Aboubakar heads a stoppage-time winner against Brazil, then is sent off for celebrating: Cameroon's first ever World Cup win over Brazil." },
  { id: 3857259, note: "Two goals down to Serbia, Cameroon come back to 3-3 in the second half." },
  { id: 3857290, note: "Breel Embolo, born in Yaoundé, scores for Switzerland and refuses to celebrate." },
  { id: 3920413, note: "AFCON 2023, a must-win game: 1-0 up, then 1-2 down with five minutes left, then an own goal and Christopher Wooh's stoppage-time winner." },
  { id: 3922238, note: "AFCON 2023 round of 16: Ademola Lookman's two goals send Cameroon home." },
];

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

let tracked = new Set();   // matches with real player tracking (World Cup 2022): playable in 3D

function card(m, extra = "", cls = "") {
  const [h, a] = m.score;
  const pens = m.shootout && (m.shootout[0] || m.shootout[1]) ? `pens ${m.shootout[0]}-${m.shootout[1]}` : "";
  const homeWon = h > a || (h === a && m.shootout && m.shootout[0] > m.shootout[1]);
  const awayWon = a > h || (h === a && m.shootout && m.shootout[1] > m.shootout[0]);
  return `<a class="card ${cls}" href="match.html?id=${m.id}">
    <div class="meta"><span>${esc(m.stage)}${tracked.has(m.id) ? ` <span class="three" data-href="play3d.html?id=${m.id}" title="Real player tracking: watch it in 3D">3D</span>` : ""}</span><span>${esc(m.date)}${pens ? " · " + pens : ""}</span></div>
    <div class="teams">
      <span class="${homeWon ? "won" : ""}">${esc(m.home)}</span><b>${h}</b>
      <span class="${awayWon ? "won" : ""}">${esc(m.away)}</span><b>${a}</b>
    </div>${extra}</a>`;
}

async function main() {
  const index = await (await fetch("data/index.json")).json();
  try {
    const r = await fetch("tracking/index.json");
    if (r.ok) tracked = new Set((await r.json()).map((t) => t.match_id));
  } catch { /* no tracking published: 2D only */ }
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".three");
    if (chip) { e.preventDefault(); location.href = chip.dataset.href; }
  });
  const all = new Map(index.flatMap((g) => g.matches.map((m) => [m.id, { ...m, comp: g.name }])));
  $("count").textContent = `${all.size} matches`;

  $("story").innerHTML = STORY.filter((s) => all.has(s.id))
    .map((s) => card(all.get(s.id), `<div class="note">${esc(s.note)}</div><div class="meta"><span>${esc(all.get(s.id).comp)}</span></div>`, "featured"))
    .join("");

  let current = index[0].key;
  const render = () => {
    const q = $("search").value.trim().toLowerCase();
    const groups = q ? index : index.filter((g) => g.key === current);
    const matches = groups.flatMap((g) => g.matches.map((m) => ({ ...m, comp: g.name })))
      .filter((m) => !q || m.home.toLowerCase().includes(q) || m.away.toLowerCase().includes(q));
    $("grid").innerHTML = matches.map((m) => card(m, q ? `<div class="meta"><span>${esc(m.comp)}</span></div>` : "")).join("")
      || `<p class="how">No match found.</p>`;
    for (const t of $("tabs").children) t.setAttribute("aria-selected", !q && t.dataset.key === current ? "true" : "false");
  };
  $("tabs").innerHTML = index.map((g) => `<button class="tab" role="tab" data-key="${g.key}">${esc(g.name)}</button>`).join("");
  for (const t of $("tabs").children) t.onclick = () => { current = t.dataset.key; $("search").value = ""; render(); };
  $("search").oninput = render;
  render();
}

main().catch((e) => { $("grid").innerHTML = `<p class="how">Could not load the matches: ${esc(e.message)}</p>`; });
