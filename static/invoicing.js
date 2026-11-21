// New invoice form: more line rows, and totals that follow what is typed.
// The totals are worked out by the server (nothing is saved); without JavaScript the form still works.
(function () {
  "use strict";
  var form = document.getElementById("new-invoice");
  if (!form) return;
  var body = document.getElementById("ni-lines");
  var more = document.getElementById("ni-more");
  var max = parseInt(form.getAttribute("data-max"), 10) || 50;

  function rows() { return body.querySelectorAll("tr[data-line]"); }
  function setText(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }

  function showPrice(select) {
    var option = select.options[select.selectedIndex];
    var price = select.closest("tr").querySelector("input[name^='price_']");
    price.placeholder = (option && option.getAttribute("data-price")) || "";
  }

  // Add a line: a copy of the last row, emptied (without JavaScript the same button asks the server)
  if (more) {
    more.type = "button";
    more.addEventListener("click", function () {
      var all = rows();
      var n = all.length + 1;
      if (n > max) return;
      var row = all[all.length - 1].cloneNode(true);
      row.setAttribute("data-line", n);
      row.querySelector("td.num").textContent = n;
      row.querySelector("td.value").textContent = "";
      row.querySelectorAll("option[data-gone]").forEach(function (o) { o.remove(); });
      row.querySelectorAll("select, input").forEach(function (el) {
        el.name = el.name.replace(/_\d+$/, "_" + n);
        el.setAttribute("aria-label", el.getAttribute("aria-label").replace(/\d+$/, n));
        if (el.tagName === "SELECT") { el.selectedIndex = 0; } else { el.value = ""; }
      });
      showPrice(row.querySelector("select"));
      body.appendChild(row);
      if (n >= max) more.hidden = true;
      row.querySelector("select").focus();
    });
  }

  // one click issues one invoice: the button is switched off once the form is on its way
  // (the server also refuses the same form twice, by its token)
  form.addEventListener("submit", function (e) {
    var button = document.getElementById("ni-issue");
    if (e.submitter === button) setTimeout(function () { button.disabled = true; button.textContent = "Issuing…"; }, 0);
  });
  window.addEventListener("pageshow", function () {
    var button = document.getElementById("ni-issue");
    button.disabled = false;
    button.textContent = "Issue invoice and add to Income";
  });

  // Enter in a field does not issue the invoice; only the button does
  form.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.tagName === "INPUT") e.preventDefault();
  });

  // the due date and the date of payment cannot be before the invoice date (the server checks this too)
  var date = document.getElementById("ni-date");
  date.addEventListener("change", function () {
    document.getElementById("ni-due").min = this.value;
    document.getElementById("ni-paid").min = this.value;
  });

  var timer = null, asked = 0;
  function refresh() {
    var mine = ++asked;
    fetch("/invoicing/totals", { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (t) {
        if (!t || mine !== asked) return;
        var values = {};
        t.lines.forEach(function (l) { values[l.line] = l.value_display + " " + l.currency; });
        rows().forEach(function (row) { row.querySelector("td.value").textContent = values[row.getAttribute("data-line")] || ""; });
        // lines in different currencies have no total (the server sends it empty)
        function amount(value) { return value ? value + " " + t.currency : "—"; }
        setText("ni-subtotal", amount(t.subtotal));
        setText("ni-vat", amount(t.vat));
        setText("ni-total", amount(t.total));
        var status = document.getElementById("ni-problems");
        status.textContent = t.error ? "Still needed before issuing: " + t.error + "." : "Ready to be issued.";
        status.className = "small " + (t.error ? "warn" : "muted");
        setText("ni-next", t.next_number ? "· this will be " + t.next_number : "");
      })
      .catch(function () { /* the totals stay as they were; issuing checks everything again */ });
  }
  function later() { clearTimeout(timer); timer = setTimeout(refresh, 250); }
  form.addEventListener("input", later);
  form.addEventListener("change", function (e) {
    if (e.target.tagName === "SELECT" && /^product_id_/.test(e.target.name)) showPrice(e.target);
    later();
  });
})();

// Credit note form: "Credit all" and totals that follow what is typed, like the New invoice form.
// The totals are worked out by the server (nothing is saved); without JavaScript the form still works.
(function () {
  "use strict";
  var form = document.getElementById("credit-note");
  if (!form) return;
  var url = form.getAttribute("data-totals");
  function setText(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }

  // Credit all: every line gets what is left of it (without JavaScript the same button asks the server)
  var all = document.getElementById("cn-all");
  if (all) {
    all.type = "button";
    all.addEventListener("click", function () {
      form.querySelectorAll("input[data-left]").forEach(function (el) { el.value = el.getAttribute("data-left"); });
      refresh();
    });
  }

  // one click issues one credit note (the server also refuses the same form twice, by its token)
  form.addEventListener("submit", function (e) {
    var button = document.getElementById("cn-issue");
    if (e.submitter === button) setTimeout(function () { button.disabled = true; button.textContent = "Issuing…"; }, 0);
  });
  window.addEventListener("pageshow", function () {
    var button = document.getElementById("cn-issue");
    button.disabled = false;
    button.textContent = "Issue credit note and add to Income";
  });

  // Enter in a field does not issue the credit note; only the button does
  form.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.tagName === "INPUT") e.preventDefault();
  });

  // the refund cannot be before the credit note (the server checks this too)
  document.getElementById("cn-date").addEventListener("change", function () {
    document.getElementById("cn-refund").min = this.value;
  });

  var timer = null, asked = 0;
  function refresh() {
    var mine = ++asked;
    fetch(url, { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (t) {
        if (!t || mine !== asked) return;
        var values = {};
        t.lines.forEach(function (l) { values[l.line] = l.value_display + " " + t.currency; });
        form.querySelectorAll("tr[data-line]").forEach(function (row) {
          row.querySelector("td.value").textContent = values[row.getAttribute("data-line")] || "";
        });
        setText("cn-subtotal", t.subtotal + " " + t.currency);
        setText("cn-vat", t.vat + " " + t.currency);
        setText("cn-total", t.total + " " + t.currency);
        var status = document.getElementById("cn-problems");
        status.textContent = t.error ? "Still needed before issuing: " + t.error + "." : "Ready to be issued.";
        status.className = "small " + (t.error ? "warn" : "muted");
        setText("cn-next", t.next_number ? "· this will be " + t.next_number : "");
      })
      .catch(function () { /* the totals stay as they were; issuing checks everything again */ });
  }
  function later() { clearTimeout(timer); timer = setTimeout(refresh, 250); }
  form.addEventListener("input", later);
  form.addEventListener("change", later);
})();
