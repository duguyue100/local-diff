(() => {
  const $ = (selector) => document.querySelector(selector);
  const diffEl = $("#diff");
  const views = ["line-by-line", "side-by-side"];

  let view = localStorage.getItem("local-diff-view") || "line-by-line";
  if (!views.includes(view)) view = "line-by-line";
  let current = null;
  let requestId = 0;

  function setView(next) {
    view = next;
    localStorage.setItem("local-diff-view", view);
    document.querySelectorAll(".segmented button").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function fillSelect(select, names, selected) {
    select.textContent = "";
    if (!names.includes(selected)) names = [selected, ...names];
    for (const name of names) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      option.selected = name === selected;
      select.append(option);
    }
  }

  function countLines(patch) {
    let additions = 0;
    let deletions = 0;
    let inHunk = false;
    for (const line of patch.split("\n")) {
      if (line.startsWith("diff --git ")) {
        inHunk = false;
      } else if (line.startsWith("@@")) {
        inHunk = true;
      } else if (inHunk && line.startsWith("+")) {
        additions++;
      } else if (inHunk && line.startsWith("-")) {
        deletions++;
      }
    }
    return { additions, deletions };
  }

  function render(data) {
    const names = data.branches.slice();
    fillSelect($("#base"), names, data.base);
    fillSelect($("#head"), names, data.head);

    const repoName = data.name || data.repo.split("/").filter(Boolean).pop() || data.repo;
    $("#repo-name").textContent = repoName;
    $("#repo-name").title = data.repo;
    document.title = `${repoName} · local-diff`;
    $("#working-badge").hidden = !data.workingTree;

    const hasPatch = data.patch.trim() !== "";
    $("#empty").hidden = hasPatch;
    $("#summary").hidden = !hasPatch;
    diffEl.textContent = "";
    if (hasPatch) {
      const { additions, deletions } = countLines(data.patch);
      $("#summary-text").textContent = data.workingTree
        ? `Comparing working tree to ${data.base}`
        : `Comparing ${data.base} to ${data.head}`;
      $("#summary-stats").innerHTML = `<span class="add">+${additions}</span> <span class="del">−${deletions}</span>`;
      new Diff2HtmlUI(diffEl, data.patch, {
        drawFileList: true,
        fileListToggle: true,
        fileListStartVisible: true,
        fileContentToggle: true,
        matching: "lines",
        outputFormat: view,
        synchronisedScroll: true,
        highlight: true,
        renderNothingWhenEmpty: true,
        stickyFileHeaders: true,
      }).draw();
    }

    const url = new URL(location.href);
    url.searchParams.set("base", data.base);
    url.searchParams.set("head", data.head);
    history.replaceState(null, "", url);
  }

  function showError(message) {
    const error = $("#error");
    error.textContent = message;
    error.hidden = false;
    diffEl.textContent = "";
    $("#empty").hidden = true;
    $("#summary").hidden = true;
  }

  async function load(base, head, fallback = false) {
    const id = ++requestId;
    document.body.classList.add("is-loading");
    $("#error").hidden = true;
    const params = new URLSearchParams();
    if (base) params.set("base", base);
    if (head) params.set("head", head);

    try {
      const response = await fetch(`/api/diff?${params}`, { cache: "no-store" });
      const data = await response.json().catch(() => ({ error: response.statusText }));
      if (!response.ok) throw new Error(data.error || response.statusText);
      if (id !== requestId) return;
      current = data;
      render(data);
    } catch (error) {
      if (id !== requestId) return;
      if ((base || head) && !fallback) {
        load(undefined, undefined, true);
        return;
      }
      showError(error.message);
    } finally {
      if (id === requestId) document.body.classList.remove("is-loading");
    }
  }

  document.querySelectorAll(".segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.view === view) return;
      setView(button.dataset.view);
      if (current) render(current);
    });
  });

  $("#base").addEventListener("change", () => load($("#base").value, $("#head").value));
  $("#head").addEventListener("change", () => load($("#base").value, $("#head").value));

  setView(view);
  const params = new URLSearchParams(location.search);
  load(params.get("base"), params.get("head"));
})();
