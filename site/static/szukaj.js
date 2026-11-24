// Gmina search: szukaj.json index, results as a list of links (without JS the form leads to the
// gmina list). Results are announced to screen readers via aria-live.
(function () {
  var form = document.querySelector("[data-search]");
  if (!form) return;
  var input = form.querySelector("input");
  var list = document.getElementById("wyniki");
  var status = document.getElementById("wyniki-status");
  var base = document.querySelector('link[href$="static/site.css"]').getAttribute("href").replace("static/site.css", "");
  var index = null;
  function norm(s) { return s.toLocaleLowerCase("pl").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ł/g, "l"); }
  function render(q) {
    list.innerHTML = "";
    if (!index || q.length < 2) { status.textContent = ""; return; }
    var nq = norm(q);
    var hits = index.filter(function (g) { return norm(g.n + " " + g.p + " " + g.w).indexOf(nq) !== -1; });
    hits.sort(function (a, b) { return (norm(a.n).indexOf(nq) === 0 ? 0 : 1) - (norm(b.n).indexOf(nq) === 0 ? 0 : 1) || a.n.localeCompare(b.n, "pl"); });
    hits.slice(0, 12).forEach(function (g) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = base + g.u;
      a.textContent = g.n;
      var meta = document.createElement("span");
      meta.className = "small";
      meta.textContent = g.t + (g.p ? " · pow. " + g.p : "") + " · " + g.w;
      a.appendChild(meta);
      li.appendChild(a);
      list.appendChild(li);
    });
    status.textContent = hits.length ? "Wyniki: " + Math.min(hits.length, 12) + " z " + hits.length : "Brak gmin dla zapytania";
  }
  fetch(base + "szukaj.json").then(function (r) { return r.json(); }).then(function (d) {
    index = d;
    var q = new URLSearchParams(location.search).get("q");
    if (q) { input.value = q; render(q); }
  });
  input.addEventListener("input", function () { render(input.value.trim()); });
  form.addEventListener("submit", function (e) {
    var first = list.querySelector("a");
    if (first) { e.preventDefault(); first.focus(); }
  });
})();
